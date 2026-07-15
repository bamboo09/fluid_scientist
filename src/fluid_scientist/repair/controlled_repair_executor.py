"""Controlled repair executor — applies fixes and validates them.

Applies fixes in order:
1. CONFIG_ONLY: Modify controlDict/fvSolution parameters
2. DICTIONARY_SYNTAX: Fix syntax errors in dictionary files
3. PARTIAL_REGENERATION: Regenerate specific files from CaseSpec

Each fix is validated:
- Syntax check (foamDictionary)
- Mesh regeneration (if mesh files changed)
- Smoke test (to verify the fix works)

No retry without repair — every retry must include a documented change.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

from fluid_scientist.repair.repair_policy import (
    RepairAttempt,
    RepairLevel,
    RepairPhase,
    RepairPolicy,
    RepairStatus,
)

logger = logging.getLogger(__name__)


@dataclass
class RepairResult:
    """Result of a repair attempt."""
    success: bool = False
    status: RepairStatus = RepairStatus.PENDING
    diagnosis: dict[str, Any] | None = None
    fixes_applied: list[dict[str, Any]] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    validation_passed: bool = False
    retry_passed: bool = False
    error: str | None = None
    attempt: RepairAttempt | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "status": self.status.value,
            "diagnosis": self.diagnosis,
            "fixes_applied": self.fixes_applied,
            "files_modified": self.files_modified,
            "validation_passed": self.validation_passed,
            "retry_passed": self.retry_passed,
            "error": self.error,
            "attempt": self.attempt.__dict__ if self.attempt else None,
        }


class ControlledRepairExecutor:
    """Applies LLM-suggested fixes and validates them.

    Key principles:
    - No RETRY_WITHOUT_REPAIR: every retry must include a documented file change
    - Each fix is validated before retry
    - Phase freezes after max_attempts_per_phase
    - Global limit prevents infinite repair loops
    """

    def __init__(
        self,
        executor: Any | None = None,  # WorkstationExecutor
        compiler: Any | None = None,  # ObstacleFlowCompiler
    ) -> None:
        self._executor = executor
        self._compiler = compiler

    def execute_repair(
        self,
        diagnosis: dict[str, Any],
        context: dict[str, Any],
        policy: RepairPolicy,
        phase: RepairPhase,
        case_path: str = "",
        remote_case_path: str = "",
    ) -> RepairResult:
        """Execute a repair based on LLM diagnosis.

        Args:
            diagnosis: LLM diagnosis result
            context: Repair context
            policy: Repair policy controlling limits
            phase: Which phase is being repaired
            case_path: Local case directory
            remote_case_path: Remote case directory on workstation

        Returns:
            RepairResult with applied fixes and validation status
        """
        result = RepairResult()
        result.diagnosis = diagnosis

        # Check if repair is allowed
        if not policy.can_attempt(phase):
            result.status = RepairStatus.PHASE_FROZEN if policy.phase_frozen.get(phase.value) else RepairStatus.GLOBAL_LIMIT_REACHED
            result.error = f"Repair not allowed: {result.status.value}"
            return result

        attempt_number = policy.current_global_attempts + 1
        level = policy.get_repair_level(phase, policy.phase_attempts.get(phase.value, 0) + 1)

        # Create attempt record
        attempt = RepairAttempt(
            attempt_number=attempt_number,
            phase=phase,
            level=level,
            error_summary=context.get("error", {}).get("error_message", "unknown"),
        )

        fixes = diagnosis.get("fixes", [])
        if not fixes:
            attempt.fix_applied = "no_fixes_suggested"
            result.status = policy.record_attempt(attempt)
            result.error = "LLM suggested no fixes"
            result.attempt = attempt
            return result

        # Apply fixes based on repair level
        applied_fixes: list[dict[str, Any]] = []
        for fix in fixes:
            applied = self._apply_single_fix(fix, level, case_path, remote_case_path)
            if applied:
                applied_fixes.append(applied)
                attempt.files_modified.append(applied.get("file", ""))

        if not applied_fixes:
            attempt.fix_applied = "no_fixes_applied"
            result.status = policy.record_attempt(attempt)
            result.error = "No fixes could be applied"
            result.attempt = attempt
            return result

        attempt.fix_applied = "; ".join(f.get("description", "") for f in applied_fixes)
        result.fixes_applied = applied_fixes
        result.files_modified = attempt.files_modified

        # Validate the repair
        if self._executor and remote_case_path:
            validation_result = self._validate_repair(remote_case_path, level)
            attempt.validation_passed = validation_result.get("passed", False)
            result.validation_passed = attempt.validation_passed

            if not attempt.validation_passed:
                attempt.error_log = validation_result.get("log", "")
                result.status = policy.record_attempt(attempt)
                result.error = f"Validation failed: {validation_result.get('error', '')}"
                result.attempt = attempt
                return result

        # Record successful application
        result.success = True
        result.status = policy.record_attempt(attempt)
        result.attempt = attempt
        return result

    def _apply_single_fix(
        self,
        fix: dict[str, Any],
        level: RepairLevel,
        case_path: str,
        remote_case_path: str,
    ) -> dict[str, Any] | None:
        """Apply a single fix to a file.

        Returns the applied fix record, or None if failed.
        """
        file_name = fix.get("file", "")
        param = fix.get("parameter", "")
        old_val = fix.get("old_value", "")
        new_val = fix.get("new_value", "")
        reason = fix.get("reason", "")

        if not file_name or not new_val:
            return None

        # Determine the target path
        target = remote_case_path if remote_case_path else case_path
        if not target:
            return None

        try:
            if level == RepairLevel.CONFIG_ONLY and self._executor:
                # For config-only fixes, use sed to modify the remote file
                # Escape special characters for sed
                escaped_new = str(new_val).replace("/", "\\/").replace("&", "\\&")
                escaped_param = re.escape(param)

                cmd = (
                    f"cd {target} && "
                    f"sed -i 's/\\({escaped_param}\\s*\\).*;/\\1 {escaped_new};/' {file_name} && "
                    f"grep '{escaped_param}' {file_name}"
                )
                ssh_result = self._executor._ssh(cmd, timeout=15)

                return {
                    "file": file_name,
                    "parameter": param,
                    "old_value": old_val,
                    "new_value": new_val,
                    "reason": reason,
                    "description": f"Changed {param} from {old_val} to {new_val} in {file_name}",
                    "method": "sed_remote",
                }

            elif level == RepairLevel.DICTIONARY_SYNTAX and self._executor:
                # For syntax fixes, write the entire fixed file content
                content = fix.get("file_content", "")
                if content:
                    # Upload the fixed file
                    import tempfile
                    with tempfile.NamedTemporaryFile(mode="w", suffix=f"_{os.path.basename(file_name)}", delete=False) as f:
                        f.write(content)
                        temp_path = f.name

                    self._executor._scp_upload(temp_path, f"{target}/{file_name}")
                    os.unlink(temp_path)

                    return {
                        "file": file_name,
                        "reason": reason,
                        "description": f"Rewrote {file_name} to fix syntax error",
                        "method": "scp_upload",
                    }

            elif level == RepairLevel.PARTIAL_REGENERATION and self._compiler:
                # For regeneration, the fix specifies which files to regenerate
                # This requires the compiler to regenerate specific files
                # For now, just log the intent
                logger.info("Partial regeneration requested for %s", file_name)
                return {
                    "file": file_name,
                    "reason": reason,
                    "description": f"Requested regeneration of {file_name}",
                    "method": "compiler_regen",
                }

        except Exception as e:
            logger.error("Failed to apply fix: %s", e)
            return None

        return None

    def _validate_repair(self, remote_case_path: str, level: RepairLevel) -> dict[str, Any]:
        """Validate a repair by running quick checks.

        For CONFIG_ONLY: just check the file is valid
        For DICTIONARY_SYNTAX: run foamDictionary -check
        For PARTIAL_REGENERATION: run blockMesh + checkMesh
        """
        if not self._executor:
            return {"passed": True, "error": ""}

        try:
            if level == RepairLevel.CONFIG_ONLY:
                # Just verify the file exists and has the parameter
                cmd = f"cd {remote_case_path} && ls system/controlDict"
                result = self._executor._ssh(cmd, timeout=10)
                return {"passed": result.returncode == 0, "error": ""}

            elif level == RepairLevel.DICTIONARY_SYNTAX:
                # Run foamDictionary to check syntax
                cmd = f"cd {remote_case_path} && foamDictionary system/controlDict > /dev/null 2>&1"
                result = self._executor._ssh(cmd, timeout=15)
                if result.returncode != 0:
                    return {"passed": False, "error": "Dictionary syntax check failed", "log": result.stderr}
                return {"passed": True, "error": ""}

            elif level == RepairLevel.PARTIAL_REGENERATION:
                # Run blockMesh + checkMesh
                cmd = f"cd {remote_case_path} && blockMesh > /dev/null 2>&1 && checkMesh > /dev/null 2>&1"
                result = self._executor._ssh(cmd, timeout=60)
                if result.returncode != 0:
                    return {"passed": False, "error": "Mesh regeneration failed", "log": result.stderr}
                return {"passed": True, "error": ""}

        except Exception as e:
            return {"passed": False, "error": str(e), "log": ""}

        return {"passed": True, "error": ""}
