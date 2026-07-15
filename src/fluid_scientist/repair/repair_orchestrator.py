"""Repair orchestrator — coordinates the full repair loop.

Flow:
1. Error classified by OpenFOAMErrorClassifier
2. Context built by RepairContextBuilder
3. LLM diagnosis by LLMDiagnoser
4. Fix applied by ControlledRepairExecutor
5. Validation: syntax check → blockMesh → smoke test
6. If validation passes, retry the failed stage
7. If retry passes, resume normal flow
8. If retry fails, escalate to next repair level
9. After 3 attempts, freeze phase and return failure
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from fluid_scientist.repair.controlled_repair_executor import (
    ControlledRepairExecutor,
    RepairResult,
)
from fluid_scientist.repair.error_classifier import (
    ClassifiedError,
    OpenFOAMErrorClassifier,
)
from fluid_scientist.repair.repair_context_builder import RepairContextBuilder
from fluid_scientist.repair.repair_policy import (
    RepairPhase,
    RepairPolicy,
    RepairStatus,
)
from fluid_scientist.repair.llm_diagnoser import LLMDiagnoser

logger = logging.getLogger(__name__)


@dataclass
class RepairOrchestrationResult:
    """Final result of the repair orchestration."""
    repaired: bool = False
    attempts: int = 0
    final_status: RepairStatus = RepairStatus.PENDING
    diagnosis_history: list[dict] = field(default_factory=list)
    fixes_applied: list[dict] = field(default_factory=list)
    error: str | None = None
    policy_snapshot: dict | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "repaired": self.repaired,
            "attempts": self.attempts,
            "final_status": self.final_status.value,
            "diagnosis_history": self.diagnosis_history,
            "fixes_applied": self.fixes_applied,
            "error": self.error,
            "policy_snapshot": self.policy_snapshot,
        }


class RepairOrchestrator:
    """Coordinates the full repair loop for OpenFOAM execution failures.

    Usage:
        orchestrator = RepairOrchestrator(
            executor=workstation_executor,
            compiler=obstacle_flow_compiler,
            llm_client=llm_client,
        )
        result = orchestrator.attempt_repair(
            error_log=smoke_report["output_tail"],
            stage="smoke",
            spec=spec,
            case_path=case_path,
            remote_case_path=remote_case_path,
            user_text=user_text,
        )
        if result.repaired:
            # Retry smoke test
            smoke_report = executor.run_smoke_test(remote_case_path)
    """

    def __init__(
        self,
        executor: Any | None = None,
        compiler: Any | None = None,
        llm_client: Any | None = None,
    ) -> None:
        self._classifier = OpenFOAMErrorClassifier()
        self._context_builder = RepairContextBuilder()
        self._diagnoser = LLMDiagnoser(llm_client=llm_client)
        self._executor_impl = ControlledRepairExecutor(
            executor=executor,
            compiler=compiler,
        )
        self._policy = RepairPolicy()

    def attempt_repair(
        self,
        error_log: str,
        stage: str,
        spec: Any | None = None,
        file_contents: dict[str, str] | None = None,
        case_path: str = "",
        remote_case_path: str = "",
        user_text: str = "",
        llm_client: Any | None = None,
    ) -> RepairOrchestrationResult:
        """Attempt to repair an OpenFOAM execution failure.

        Args:
            error_log: Raw error log from the failed stage
            stage: Which stage failed (mesh, smoke, full_run)
            spec: Current CaseSpec
            file_contents: Contents of relevant files
            case_path: Local case directory
            remote_case_path: Remote case directory
            user_text: Original user input
            llm_client: Optional override LLM client

        Returns:
            RepairOrchestrationResult with repair details
        """
        result = RepairOrchestrationResult()
        phase = RepairPhase(stage) if stage in ("mesh", "smoke", "full_run") else RepairPhase.SMOKE

        # Classify the error
        errors = self._classifier.classify(error_log, stage=stage)
        primary_error = self._classifier.get_primary_error(errors)

        if primary_error is None:
            result.error = "No classifiable error found in log"
            result.final_status = RepairStatus.FAILED
            return result

        # If error is not repairable, skip repair
        if not primary_error.is_repairable:
            result.error = f"Error category {primary_error.category.value} is not repairable"
            result.final_status = RepairStatus.FAILED
            return result

        # Attempt repair loop
        while self._policy.can_attempt(phase):
            # Build context
            context = self._context_builder.build_context(
                error=primary_error,
                stage=stage,
                spec=spec,
                file_contents=file_contents,
                user_text=user_text,
                previous_attempts=[a.__dict__ for a in self._policy.attempt_history],
            )

            # Diagnose with LLM
            diagnosis = self._diagnoser.diagnose(context, llm_client=llm_client)
            result.diagnosis_history.append(diagnosis)

            if not diagnosis.get("fixes"):
                logger.warning("LLM suggested no fixes, escalating repair level")
                # Force escalation by recording a failed attempt
                from fluid_scientist.repair.repair_policy import RepairAttempt, RepairLevel
                attempt = RepairAttempt(
                    attempt_number=self._policy.current_global_attempts + 1,
                    phase=phase,
                    level=self._policy.get_repair_level(phase, self._policy.phase_attempts.get(phase.value, 0) + 1),
                    error_summary=primary_error.error_message,
                    fix_applied="no_fixes_suggested",
                )
                self._policy.record_attempt(attempt)
                continue

            # Apply repair
            repair_result = self._executor_impl.execute_repair(
                diagnosis=diagnosis,
                context=context,
                policy=self._policy,
                phase=phase,
                case_path=case_path,
                remote_case_path=remote_case_path,
            )

            result.fixes_applied.extend(repair_result.fixes_applied)

            if repair_result.status == RepairStatus.SUCCESS:
                # Validation passed — but we need to verify by retrying
                result.repaired = True
                result.attempts = self._policy.current_global_attempts
                result.final_status = RepairStatus.SUCCESS
                result.policy_snapshot = self._policy.to_dict()
                return result

            elif repair_result.status == RepairStatus.PHASE_FROZEN:
                result.error = f"Phase {phase.value} frozen after {self._policy.max_attempts_per_phase} attempts"
                result.final_status = RepairStatus.PHASE_FROZEN
                result.policy_snapshot = self._policy.to_dict()
                return result

            elif repair_result.status == RepairStatus.GLOBAL_LIMIT_REACHED:
                result.error = f"Global repair limit ({self._policy.max_global_attempts}) reached"
                result.final_status = RepairStatus.GLOBAL_LIMIT_REACHED
                result.policy_snapshot = self._policy.to_dict()
                return result

            # Else: failed but can retry — continue loop
            logger.info("Repair attempt failed, retrying...")

        result.attempts = self._policy.current_global_attempts
        result.error = result.error or "Repair loop exhausted"
        result.final_status = RepairStatus.FAILED
        result.policy_snapshot = self._policy.to_dict()
        return result

    @property
    def policy(self) -> RepairPolicy:
        """Access the current repair policy."""
        return self._policy

    def reset_policy(self) -> None:
        """Reset the repair policy for a new case."""
        self._policy = RepairPolicy()
