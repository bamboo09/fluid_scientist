"""CodeExtensionSpec and closed-loop workflow for code extension generation.

This module implements the draft-workflow closed loop::

    spec_draft -> spec_reviewed -> generating -> generated -> testing -> tested
        -> approved -> registered
        \\-> rejected (from any non-terminal state)

The workflow bridges :class:`~fluid_scientist.capabilities.models.MissingCapability`
detection to :class:`~fluid_scientist.capabilities.models.CapabilityRegistry`
registration through a safety-gated, human-in-the-loop process.
"""

from __future__ import annotations

import copy
from datetime import datetime
from typing import Any, ClassVar, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from fluid_scientist.capabilities.models import CapabilityRegistry
from fluid_scientist.case_plan.models import MissingCapability
from fluid_scientist.compat import UTC

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

ExtensionType = Literal[
    "python_metric",
    "openfoam_function_object_writer",
    "case_compiler_plugin",
    "boundary_condition_writer",
    "mesh_generator_plugin",
    "geometry_generator_plugin",
    "initial_condition_generator",
    "analysis_plugin",
    "metric_operator",
    "parameter_definition",
    "solver_extension",
    "post_processor",
    # Legacy types kept for backward compatibility
    "boundary_condition",
    "geometry_generator",
    "physical_model_writer",
    "postprocess_metric",
    "mesh_generator",
]

SpecStatus = Literal[
    # v5 workflow states
    "spec_draft",
    "spec_reviewed",
    "generating",
    "generated",
    "testing",
    "tested",
    "approved",
    "rejected",
    "registered",
    # Legacy workflow states (capabilities.models.CodeExtensionSpec)
    "draft",
    "sandbox_tested",
    "auto_tested",
    "conditionally_approved",
    "revision_required",
    "sandbox_only",
]

# ---------------------------------------------------------------------------
# Default safety constraints
# ---------------------------------------------------------------------------

DEFAULT_SAFETY_CONSTRAINTS: list[dict[str, Any]] = [
    {
        "constraint_id": "fs_access",
        "type": "file_system",
        "description": (
            "File system access limited to read-only for case files, "
            "write only to designated output directories."
        ),
        "read_paths": ["case/**"],
        "write_paths": ["output/**"],
        "enforcement": "sandbox_path_filter",
    },
    {
        "constraint_id": "shell_exec",
        "type": "shell_execution",
        "description": (
            "No arbitrary shell commands permitted; only allowlisted "
            "executables may be invoked with a mandatory timeout."
        ),
        "allowlisted_commands": [],
        "enforcement": "command_allowlist",
    },
    {
        "constraint_id": "exec_timeout",
        "type": "execution_timeout",
        "description": "Maximum execution time of 300 seconds.",
        "max_seconds": 300,
        "enforcement": "timeout_guard",
    },
    {
        "constraint_id": "numerical_safety",
        "type": "numerical_safety",
        "description": "NaN and Inf detection with bounded output enforcement.",
        "checks": ["nan_detection", "inf_detection", "bounded_output"],
        "output_bounds": {"min": -1e15, "max": 1e15},
        "enforcement": "post_execution_validator",
    },
]


def _default_safety_constraints() -> list[dict[str, Any]]:
    """Return a deep copy of the default safety constraints."""
    return copy.deepcopy(DEFAULT_SAFETY_CONSTRAINTS)


# ---------------------------------------------------------------------------
# Capability-type to extension-type mapping
# ---------------------------------------------------------------------------

_CAPABILITY_TYPE_MAP: dict[str, str] = {
    "metric_operator": "metric_operator",
    "boundary_condition": "boundary_condition",
    "solver_extension": "physical_model_writer",
    "physical_model_writer": "physical_model_writer",
    "post_processor": "postprocess_metric",
    "postprocess_metric": "postprocess_metric",
    "mesh_generator": "mesh_generator",
    "geometry_generator": "geometry_generator",
    "parameter_definition": "analysis_plugin",
    "analysis_plugin": "analysis_plugin",
}

_VALID_EXTENSION_TYPES: frozenset[str] = frozenset(
    {
        "python_metric",
        "openfoam_function_object_writer",
        "case_compiler_plugin",
        "boundary_condition_writer",
        "mesh_generator_plugin",
        "geometry_generator_plugin",
        "initial_condition_generator",
        "analysis_plugin",
        "metric_operator",
        "parameter_definition",
        "solver_extension",
        "post_processor",
        # Legacy types kept for backward compatibility
        "boundary_condition",
        "geometry_generator",
        "physical_model_writer",
        "postprocess_metric",
        "mesh_generator",
    }
)


# ---------------------------------------------------------------------------
# CodeExtensionSpec model
# ---------------------------------------------------------------------------


class CodeExtensionSpec(BaseModel):
    """Specification for a code extension generated to fill a capability gap.

    Lifecycle (v5)::

        spec_draft -> spec_reviewed -> generating -> generated -> testing -> tested
            -> approved -> registered
            \\-> rejected (from any non-terminal state)

    Lifecycle (legacy)::

        draft -> sandbox_tested -> auto_tested -> approved -> registered
                                              \\-> conditionally_approved -> registered
                                              \\-> rejected
        draft -> rejected

    This model unifies the v5 spec (from ``code_extension/spec.py``) and the
    legacy spec (from ``capabilities/models.py``).  All legacy fields are
    present as optional with backward-compatible defaults.  ``status`` is
    the canonical lifecycle field; ``state`` is provided as a property alias
    for legacy code.

    Attributes:
        extension_id: Unique identifier for this extension.
        session_id: Research session that triggered the capability gap.
        draft_id: Optional study-decomposition draft that originated the gap.
        extension_type: Category of code extension to generate.
        missing_capability_id: ID of the MissingCapability this extension fills.
        requirement: Human-readable description of what the extension must do.
        risk_level: Risk classification for the extension ("low", "medium", "high").
        files_to_create_or_modify: List of file paths the extension will touch.
        target_interfaces: Interface contracts the extension must conform to.
        inputs: Declared input parameters (name, data_type, etc.).
        outputs: Declared output parameters.
        acceptance_tests: Test cases the generated code must pass.
        safety_constraints: Safety requirements enforced during execution.
        status: Current lifecycle status.
        generated_code: The generated source code (once available).
        test_results: Results from running acceptance tests.
        review_notes: Notes from review / approval / rejection.
        created_at: Creation timestamp (UTC).
        updated_at: Last-update timestamp (UTC).
    """

    model_config = ConfigDict(populate_by_name=True)

    extension_id: str
    session_id: str = ""
    draft_id: str | None = None
    extension_type: ExtensionType = "analysis_plugin"  # type: ignore[assignment]
    missing_capability_id: str = ""
    requirement: str = ""
    risk_level: Literal["low", "medium", "high"] = "medium"
    files_to_create_or_modify: list[str] = Field(default_factory=list)
    target_interfaces: list[str] = Field(default_factory=list)
    inputs: list[dict[str, Any]] = Field(default_factory=list)
    outputs: list[dict[str, Any]] = Field(default_factory=list)
    acceptance_tests: list[dict[str, Any]] = Field(default_factory=list)
    safety_constraints: list[dict[str, Any]] = Field(
        default_factory=_default_safety_constraints
    )
    status: SpecStatus = "spec_draft"  # type: ignore[assignment]
    generated_code: str | None = None
    test_results: dict[str, Any] | None = None
    review_notes: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # -- Legacy fields (from capabilities.models.CodeExtensionSpec) ---------
    extension_name: str = ""
    rationale: str = ""
    required_inputs: list[str] = Field(default_factory=list)
    expected_outputs: list[str] = Field(default_factory=list)
    code_language: str = "python"
    unit_tests: list[dict[str, Any]] = Field(default_factory=list)
    benchmark_tests: list[dict[str, Any]] = Field(default_factory=list)
    security_checks: list[dict[str, Any]] = Field(default_factory=list)
    approval_comment: str | None = None
    approved_by: str | None = None
    approved_at: str | None = None
    related_capability_id: str | None = None
    research_session_id: str | None = None  # alias for session_id
    experiment_spec_id: str | None = None

    # -- Backward-compatible aliases ----------------------------------------

    @property
    def description(self) -> str:
        """Backward-compatible alias for :attr:`requirement`."""
        return self.requirement

    @description.setter
    def description(self, value: str) -> None:
        self.requirement = value

    @property
    def state(self) -> str:
        """Backward-compatible alias for :attr:`status`."""
        return self.status

    @state.setter
    def state(self, value: str) -> None:
        self.status = value  # type: ignore[assignment]

    @model_validator(mode="before")
    @classmethod
    def _alias_legacy_fields(cls, data: Any) -> Any:
        """Accept legacy field names as input aliases.

        Handles:
        - ``description`` -> ``requirement``
        - ``state`` -> ``status``
        - ``research_session_id`` can populate ``session_id`` if absent
        - ``related_capability_id`` can populate ``missing_capability_id`` if absent
        """
        if isinstance(data, dict):
            if "description" in data and "requirement" not in data:
                data = {**data, "requirement": data.pop("description")}
            elif "description" in data:
                data.pop("description")
            if "state" in data and "status" not in data:
                data = {**data, "status": data.pop("state")}
            elif "state" in data:
                data.pop("state")
            # research_session_id aliases to session_id (legacy -> v5)
            rsid = data.get("research_session_id")
            if rsid and not data.get("session_id"):
                data["session_id"] = rsid
            # related_capability_id aliases to missing_capability_id (legacy -> v5)
            rcid = data.get("related_capability_id")
            if rcid and not data.get("missing_capability_id"):
                data["missing_capability_id"] = rcid
        return data

    # -- State machine -------------------------------------------------------

    _VALID_TRANSITIONS: ClassVar[dict[str, frozenset[str]]] = {
        # v5 workflow
        "spec_draft": frozenset({"spec_reviewed", "rejected"}),
        "spec_reviewed": frozenset({"generating", "rejected"}),
        "generating": frozenset({"generated", "rejected"}),
        "generated": frozenset({"testing", "rejected"}),
        "testing": frozenset({"tested", "rejected"}),
        "tested": frozenset({"approved", "rejected"}),
        "approved": frozenset({"registered", "rejected"}),
        # Legacy workflow
        "draft": frozenset({"sandbox_tested", "rejected"}),
        "sandbox_tested": frozenset({"auto_tested", "rejected", "revision_required"}),
        "auto_tested": frozenset(
            {"approved", "conditionally_approved", "rejected", "revision_required"}
        ),
        "conditionally_approved": frozenset({"registered", "rejected"}),
        "revision_required": frozenset({"draft"}),
        "sandbox_only": frozenset({"approved", "rejected"}),
        # Terminal states
        "rejected": frozenset(),
        "registered": frozenset(),
    }

    _LEGACY_APPROVAL_STATES: ClassVar[frozenset[str]] = frozenset(
        {"approved", "conditionally_approved"}
    )

    def can_transition_to(self, new_status: str) -> bool:
        """Check whether *new_status* is reachable from the current status."""
        return new_status in self._VALID_TRANSITIONS.get(self.status, frozenset())

    def transition_to(
        self, new_status: str, comment: str | None = None
    ) -> CodeExtensionSpec:
        """Return a copy transitioned to *new_status*.

        Args:
            new_status: Target status/state value (v5 or legacy name).
            comment: Optional approval/transition comment (legacy compatibility).

        Raises:
            ValueError: If the transition is not valid for the current status.
        """
        if not self.can_transition_to(new_status):
            raise ValueError(
                f"Invalid transition: {self.status} -> {new_status}"
            )
        updates: dict[str, Any] = {
            "status": new_status,
            "updated_at": datetime.now(UTC),
        }
        if comment is not None:
            updates["approval_comment"] = comment
            # Also set review_notes for v5 compatibility
            existing_notes = self.review_notes
            if existing_notes:
                updates["review_notes"] = f"{existing_notes}\n{comment}"
            else:
                updates["review_notes"] = comment
        if new_status in self._LEGACY_APPROVAL_STATES:
            updates["approved_by"] = "expert"
            updates["approved_at"] = datetime.now(UTC).isoformat()
        return self.model_copy(update=updates)


# ---------------------------------------------------------------------------
# CodeExtensionWorkflow
# ---------------------------------------------------------------------------


class CodeExtensionWorkflow:
    """Manages the closed-loop code extension lifecycle.

    The workflow connects ``MissingCapability`` detection to
    ``CapabilityRegistry`` registration through a safety-gated,
    human-in-the-loop process::

        create_spec -> review_spec -> submit_for_generation -> submit_code
            -> run_tests -> approve -> register

    At any non-terminal point :meth:`reject` can terminate the workflow.
    """

    # -- 1. Spec creation ----------------------------------------------------

    def create_spec(
        self,
        missing_capability: dict[str, Any] | MissingCapability,
        session_id: str,
        draft_id: str | None = None,
    ) -> CodeExtensionSpec:
        """Create a :class:`CodeExtensionSpec` from a ``MissingCapability``.

        Args:
            missing_capability: The detected capability gap, as a dict or
                ``MissingCapability`` instance.
            session_id: Research session that triggered the gap.
            draft_id: Optional study-decomposition draft ID.

        Returns:
            A new ``CodeExtensionSpec`` in ``spec_draft`` status.
        """
        if isinstance(missing_capability, MissingCapability):
            cap = missing_capability.model_dump()
        elif isinstance(missing_capability, dict):
            cap = missing_capability
        else:
            raise TypeError(
                "missing_capability must be dict or MissingCapability, "
                f"got {type(missing_capability).__name__}"
            )

        capability_id = cap.get("capability_id", "")
        if not capability_id:
            raise ValueError("missing_capability must have a non-empty 'capability_id'")

        # Determine extension_type
        suggested = cap.get("suggested_extension_type")
        if suggested and suggested in _VALID_EXTENSION_TYPES:
            extension_type = suggested
        else:
            cap_type = cap.get("capability_type", "")
            extension_type = _CAPABILITY_TYPE_MAP.get(cap_type, "analysis_plugin")

        # Build inputs / outputs from required_inputs / expected_outputs
        required_inputs = cap.get("required_inputs", [])
        inputs = (
            [
                {"name": name, "data_type": "any", "required": True}
                for name in required_inputs
            ]
            if isinstance(required_inputs, list)
            else []
        )

        expected_outputs = cap.get("expected_outputs", [])
        outputs = (
            [{"name": name, "data_type": "any"} for name in expected_outputs]
            if isinstance(expected_outputs, list)
            else []
        )

        requirement = cap.get("requested_behavior") or cap.get("reason", "")

        return CodeExtensionSpec(
            extension_id=f"ext-{uuid4().hex[:12]}",
            session_id=session_id,
            draft_id=draft_id,
            extension_type=extension_type,  # type: ignore[arg-type]
            missing_capability_id=capability_id,
            requirement=requirement,
            target_interfaces=[],
            inputs=inputs,
            outputs=outputs,
            acceptance_tests=[],
            safety_constraints=_default_safety_constraints(),
            status="spec_draft",
        )

    # -- 2. Review -----------------------------------------------------------

    def review_spec(
        self,
        spec: CodeExtensionSpec,
        notes: str = "",
    ) -> CodeExtensionSpec:
        """Mark a spec as reviewed (``spec_draft`` -> ``spec_reviewed``).

        Args:
            spec: The spec to review.
            notes: Optional review notes.

        Returns:
            Updated spec in ``spec_reviewed`` status.
        """
        updated = spec.transition_to("spec_reviewed")
        return updated.model_copy(update={"review_notes": notes})

    # -- 3. Generation -------------------------------------------------------

    def submit_for_generation(self, spec: CodeExtensionSpec) -> CodeExtensionSpec:
        """Mark spec as ready for code generation.

        Transitions ``spec_reviewed`` -> ``generating``.

        Raises:
            ValueError: If the spec is not in ``spec_reviewed`` status.
        """
        return spec.transition_to("generating")

    def submit_code(
        self,
        spec: CodeExtensionSpec,
        code: str,
    ) -> CodeExtensionSpec:
        """Submit generated code for testing.

        Transitions ``generating`` -> ``generated`` and stores the code.

        Args:
            spec: The spec in ``generating`` status.
            code: The generated source code.

        Raises:
            ValueError: If the spec is not in ``generating`` status.
        """
        updated = spec.transition_to("generated")
        return updated.model_copy(update={"generated_code": code})

    # -- 4. Testing ----------------------------------------------------------

    def run_tests(self, spec: CodeExtensionSpec) -> CodeExtensionSpec:
        """Run acceptance tests on the generated code.

        Transitions ``generated`` -> ``testing`` -> ``tested``.

        If acceptance tests are defined, each is executed in a restricted
        sandbox. If none are defined, a basic syntax check is performed.

        Args:
            spec: The spec in ``generated`` status.

        Returns:
            Updated spec in ``tested`` status with ``test_results`` populated.

        Raises:
            ValueError: If the spec is not in ``generated`` status.
        """
        spec = spec.transition_to("testing")
        results = self._execute_acceptance_tests(spec)
        spec = spec.transition_to("tested")
        return spec.model_copy(update={"test_results": results})

    def _execute_acceptance_tests(
        self,
        spec: CodeExtensionSpec,
    ) -> dict[str, Any]:
        """Execute acceptance tests and return a results dictionary."""
        code = spec.generated_code or ""

        if not code:
            return {
                "total": 0,
                "passed": 0,
                "failed": 0,
                "results": [],
                "error": "No generated code to test",
            }

        tests = spec.acceptance_tests

        if not tests:
            # Basic validation: check that the code compiles
            try:
                compile(code, "<generated>", "exec")
                return {
                    "total": 1,
                    "passed": 1,
                    "failed": 0,
                    "results": [
                        {
                            "test_id": "syntax_check",
                            "test_name": "Syntax Check",
                            "passed": True,
                            "error": "",
                        }
                    ],
                }
            except SyntaxError as exc:
                return {
                    "total": 1,
                    "passed": 0,
                    "failed": 1,
                    "results": [
                        {
                            "test_id": "syntax_check",
                            "test_name": "Syntax Check",
                            "passed": False,
                            "error": str(exc),
                        }
                    ],
                }

        # Run each acceptance test in the sandbox
        test_results = [self._run_single_test(code, test) for test in tests]
        passed = sum(1 for r in test_results if r["passed"])
        failed = len(test_results) - passed

        return {
            "total": len(test_results),
            "passed": passed,
            "failed": failed,
            "results": test_results,
        }

    @staticmethod
    def _run_single_test(
        generated_code: str,
        test: dict[str, Any],
    ) -> dict[str, Any]:
        """Run a single acceptance test in the restricted sandbox."""
        test_id = test.get("test_id", "unknown")
        test_name = test.get("test_name", "unknown")
        test_code = test.get("test_code", "")
        timeout = min(float(test.get("timeout_seconds", 5.0)), 60.0)

        if not test_code:
            return {
                "test_id": test_id,
                "test_name": test_name,
                "passed": True,
                "error": "",
                "execution_time_s": 0.0,
            }

        combined_code = f"{generated_code}\n\n{test_code}"

        try:
            # Reuse the existing sandbox infrastructure via a legacy spec
            from fluid_scientist.code_extension.models import (
                CodeExtensionSpec as _LegacySpec,
            )
            from fluid_scientist.code_extension.models import CodeExtensionType
            from fluid_scientist.code_extension.sandbox import execute_in_sandbox

            temp_spec = _LegacySpec(
                extension_id="temp_test",
                name="temp",
                extension_type=CodeExtensionType.UTILITY,
                code=combined_code,
                language="python",
            )
            result = execute_in_sandbox(temp_spec, timeout_seconds=timeout)
            return {
                "test_id": test_id,
                "test_name": test_name,
                "passed": result.success,
                "error": result.error,
                "execution_time_s": result.execution_time_s,
                "stdout": result.stdout,
            }
        except Exception as exc:  # noqa: BLE001 - report any failure as test result
            return {
                "test_id": test_id,
                "test_name": test_name,
                "passed": False,
                "error": str(exc),
                "execution_time_s": 0.0,
            }

    # -- 5. Approval ---------------------------------------------------------

    def approve(
        self,
        spec: CodeExtensionSpec,
        review_notes: str = "",
    ) -> CodeExtensionSpec:
        """Approve the extension after tests pass.

        Transitions ``tested`` -> ``approved``.

        Args:
            spec: The spec in ``tested`` status.
            review_notes: Optional approval notes.

        Raises:
            ValueError: If the spec is not in ``tested`` status.
        """
        updated = spec.transition_to("approved")
        notes = f"Approved. {review_notes}".strip() if review_notes else "Approved."
        return updated.model_copy(update={"review_notes": notes})

    # -- 6. Rejection --------------------------------------------------------

    def reject(
        self,
        spec: CodeExtensionSpec,
        reason: str,
    ) -> CodeExtensionSpec:
        """Reject the extension from any non-terminal state.

        Transitions any non-terminal status -> ``rejected``.

        Args:
            spec: The spec to reject.
            reason: Human-readable rejection reason.

        Raises:
            ValueError: If the spec is already in a terminal state
                (``rejected`` or ``registered``).
        """
        if not spec.can_transition_to("rejected"):
            raise ValueError(
                f"Cannot reject extension in terminal state '{spec.status}'"
            )
        updated = spec.transition_to("rejected")
        return updated.model_copy(update={"review_notes": f"Rejected: {reason}"})

    # -- 7. Registration -----------------------------------------------------

    def register(
        self,
        spec: CodeExtensionSpec,
        registry: CapabilityRegistry,
    ) -> CodeExtensionSpec:
        """Register the approved extension as a new capability.

        Transitions ``approved`` -> ``registered`` and adds the extension
        to *registry* so that future capability checks recognise it.

        Args:
            spec: The spec in ``approved`` status.
            registry: The capability registry to register into.

        Raises:
            ValueError: If the spec is not in ``approved`` status.
        """
        updated = spec.transition_to("registered")
        # The unified CapabilityRegistry.register() accepts the v5
        # CodeExtensionSpec directly (it uses duck-typed attribute access
        # to handle both v5 and legacy field names).
        registry.register(updated)
        return updated


__all__ = [
    "DEFAULT_SAFETY_CONSTRAINTS",
    "CodeExtensionSpec",
    "CodeExtensionWorkflow",
    "ExtensionType",
    "SpecStatus",
]
