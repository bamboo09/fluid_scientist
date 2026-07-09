"""Apply a confirmed ChangeProposal to a draft, producing a new version.

The :class:`ApplyProposalExecutor` is the *only* component allowed to mutate
draft content.  It:

1. Validates that the proposal's ``base_draft_version`` matches the current
   draft version (optimistic concurrency).
2. Clones the draft to a new version (``version + 1``).
3. Applies each change from the proposal.
4. Runs the :class:`DraftValidator` on the result.
5. Returns the new draft and the validation result.

If any change references an unknown parameter or metric, the
:class:`UnknownParameterMapper` is consulted to flag it for the
CodeExtension workflow.
"""

from __future__ import annotations

import uuid

from fluid_scientist.draft.models import (
    ChangeProposal,
    DraftParameter,
    DraftStatus,
    ExperimentDraft,
    ParameterSource,
    ValidationResult,
)
from fluid_scientist.draft.validator import DraftValidator


class ProposalVersionMismatchError(Exception):
    """Raised when proposal's base version != draft's current version."""


class ProposalNotPendingError(Exception):
    """Raised when trying to apply a proposal that is not in 'pending' state."""


class UnknownParameterMapper:
    """Map unknown parameters/metrics to CodeExtension triggers.

    When a change proposal introduces a parameter or output that doesn't
    exist in the current draft or in the system's known parameter catalog,
    the mapper flags it as requiring a code extension rather than silently
    accepting it.
    """

    # Known parameter IDs that the system can handle natively
    _KNOWN_PARAMS: set[str] = {
        "reynolds_number", "re", "froude_number", "fr",
        "cylinder_diameter", "d", "diameter",
        "step_height", "h",
        "pipe_diameter", "domain_length", "domain_width", "domain_height",
        "inlet_velocity", "u", "velocity",
        "density", "rho", "viscosity", "nu", "mu",
        "turbulence_model", "solver",
        "time_step", "delta_t", "end_time",
        "mesh_resolution", "cell_count",
        "inclination_angle", "gap_ratio", "expansion_ratio",
        "aspect_ratio", "oscillation_amplitude", "oscillation_frequency",
        "strouhal_number", "st",
        "drag_coefficient", "cd", "lift_coefficient", "cl",
        "pressure_drop", "velocity_profile",
    }

    # Known observable/metric IDs
    _KNOWN_METRICS: set[str] = {
        "drag", "lift", "pressure", "pressure_drop",
        "velocity_profile", "strouhal", "st",
        "reynolds_stress", "turbulent_kinetic_energy", "tke",
        "wake_profile", "recirculation_length", "reattachment_length",
        "drag_coefficient", "lift_coefficient", "cd", "cl",
    }

    def check_parameter(self, param_id: str) -> dict:
        """Return whether *param_id* is known, uncertain, or missing."""
        pid_lower = param_id.lower()
        if pid_lower in self._KNOWN_PARAMS:
            return {
                "param_id": param_id,
                "status": "known",
                "requires_extension": False,
            }
        return {
            "param_id": param_id,
            "status": "missing",
            "requires_extension": True,
            "extension_type": "parameter_definition",
            "reason": f"参数 {param_id} 不在系统已知参数目录中",
        }

    def check_metric(self, metric_id: str) -> dict:
        """Return whether *metric_id* is known, uncertain, or missing."""
        mid_lower = metric_id.lower()
        if mid_lower in self._KNOWN_METRICS:
            return {
                "metric_id": metric_id,
                "status": "known",
                "requires_extension": False,
            }
        return {
            "metric_id": metric_id,
            "status": "missing",
            "requires_extension": True,
            "extension_type": "metric_operator",
            "reason": f"指标 {metric_id} 不在系统已知指标目录中",
        }


class ApplyProposalExecutor:
    """Apply a confirmed :class:`ChangeProposal` to produce a new draft version."""

    def __init__(
        self,
        validator: DraftValidator | None = None,
        unknown_mapper: UnknownParameterMapper | None = None,
    ) -> None:
        self._validator = validator or DraftValidator()
        self._unknown_mapper = unknown_mapper or UnknownParameterMapper()

    def apply(
        self,
        draft: ExperimentDraft,
        proposal: ChangeProposal,
    ) -> tuple[ExperimentDraft, ValidationResult]:
        """Apply *proposal* to *draft* and return (new_draft, validation).

        Raises:
            ProposalVersionMismatchError: if version mismatch.
            ProposalNotPendingError: if proposal is not pending.
        """
        # 1. Version check
        if proposal.base_draft_version != draft.version:
            raise ProposalVersionMismatchError(
                f"Proposal base version {proposal.base_draft_version} "
                f"!= draft version {draft.version}"
            )

        # 2. Status check
        if proposal.status != "pending":
            raise ProposalNotPendingError(
                f"Proposal status is '{proposal.status}', expected 'pending'"
            )

        # 3. Clone to new version
        new_draft_id = f"draft_{uuid.uuid4().hex[:12]}"
        new_draft = draft.clone(new_draft_id)

        # 4. Apply each change
        extension_triggers: list[dict] = []
        for change in proposal.changes:
            trigger = self._apply_change(new_draft, change)
            if trigger:
                extension_triggers.append(trigger)

        # 5. Mark proposal as applied
        proposal.status = "applied"

        # 6. Validate
        result = self._validator.validate(new_draft)

        # 7. Store extension triggers in draft
        if extension_triggers:
            new_draft.blocking_issues.extend(
                [
                    {
                        "check": "unknown_parameter_or_metric",
                        "message": t.get("reason", "Unknown parameter/metric"),
                        "trigger": t,
                    }
                    for t in extension_triggers
                ]
            )

        # 8. Update status based on validation
        if result.valid and not extension_triggers:
            new_draft.status = DraftStatus.READY
        else:
            new_draft.status = DraftStatus.DRAFT

        return new_draft, result

    # ------------------------------------------------------------------ change application
    def _apply_change(
        self, draft: ExperimentDraft, change: dict
    ) -> dict | None:
        """Apply a single change to *draft*. Returns extension trigger if any."""
        change_type = change.get("change_type", "")

        if change_type == "set_parameter":
            return self._apply_set_parameter(draft, change)
        elif change_type == "add_parameter":
            return self._apply_add_parameter(draft, change)
        elif change_type == "remove_parameter":
            self._apply_remove_parameter(draft, change)
        elif change_type == "add_output":
            self._apply_add_output(draft, change)
        elif change_type == "remove_output":
            self._apply_remove_output(draft, change)
        elif change_type == "change_boundary_condition":
            self._apply_change_bc(draft, change)
        elif change_type == "change_initial_condition":
            self._apply_change_ic(draft, change)
        elif change_type == "change_physics_model":
            self._apply_change_physics(draft, change)
        elif change_type == "change_mesh":
            self._apply_change_mesh(draft, change)
        elif change_type == "change_solver":
            self._apply_change_solver(draft, change)
        elif change_type == "change_geometry":
            self._apply_change_geometry(draft, change)
        elif change_type == "change_numerics":
            self._apply_change_numerics(draft, change)
        elif change_type in ("question", "clarification_required", "missing_capability"):
            pass  # No draft mutation needed

        return None

    def _apply_set_parameter(
        self, draft: ExperimentDraft, change: dict
    ) -> dict | None:
        target = change.get("target_path", "")
        # Extract parameter_id from "control_parameters.{id}"
        parts = target.split(".", 1)
        if len(parts) < 2:
            return None
        param_id = parts[1]
        for p in draft.control_parameters:
            if p.parameter_id == param_id:
                p.value = change.get("new_value")
                p.source = ParameterSource.USER_PROVIDED
                p.source_reason = "用户修改"
                return None
        return None

    def _apply_add_parameter(
        self, draft: ExperimentDraft, change: dict
    ) -> dict | None:
        target = change.get("target_path", "")
        parts = target.split(".", 1)
        param_id = parts[1] if len(parts) > 1 else target
        new_value = change.get("new_value")

        # Check if parameter already exists
        for p in draft.control_parameters:
            if p.parameter_id == param_id:
                p.value = new_value
                return None

        # Check if unknown
        check = self._unknown_mapper.check_parameter(param_id)
        if check["requires_extension"]:
            draft.control_parameters.append(
                DraftParameter(
                    parameter_id=param_id,
                    display_name=param_id,
                    value=new_value,
                    source=ParameterSource.UNKNOWN_REQUIRED,
                    source_reason="用户新增的未知参数",
                    category="unknown",
                )
            )
            return check

        draft.control_parameters.append(
            DraftParameter(
                parameter_id=param_id,
                display_name=param_id,
                value=new_value,
                source=ParameterSource.USER_PROVIDED,
                source_reason="用户新增参数",
            )
        )
        return None

    def _apply_remove_parameter(
        self, draft: ExperimentDraft, change: dict
    ) -> None:
        target = change.get("target_path", "")
        parts = target.split(".", 1)
        if len(parts) < 2:
            return
        param_id = parts[1]
        draft.control_parameters = [
            p for p in draft.control_parameters if p.parameter_id != param_id
        ]

    def _apply_add_output(
        self, draft: ExperimentDraft, change: dict
    ) -> None:
        new_value = change.get("new_value")
        if new_value and isinstance(new_value, dict):
            draft.requested_outputs.append(new_value)

    def _apply_remove_output(
        self, draft: ExperimentDraft, change: dict
    ) -> None:
        target = change.get("target_path", "")
        parts = target.split(".", 1)
        if len(parts) < 2:
            return
        output_name = parts[1]
        draft.requested_outputs = [
            o for o in draft.requested_outputs
            if output_name.lower() not in str(o.get("name", "")).lower()
        ]

    def _apply_change_bc(
        self, draft: ExperimentDraft, change: dict
    ) -> None:
        target = change.get("target_path", "")
        parts = target.split(".", 1)
        boundary = parts[1] if len(parts) > 1 else "unknown"
        new_value = change.get("new_value")
        if new_value:
            draft.boundary_conditions[boundary] = new_value
        else:
            draft.boundary_conditions[boundary] = {
                "status": "modified",
                "reason": change.get("reason", ""),
            }

    def _apply_change_ic(
        self, draft: ExperimentDraft, change: dict
    ) -> None:
        new_value = change.get("new_value")
        if new_value:
            draft.initial_conditions.update(new_value)
        else:
            draft.initial_conditions["status"] = "modified"

    def _apply_change_physics(
        self, draft: ExperimentDraft, change: dict
    ) -> None:
        new_value = change.get("new_value")
        if new_value:
            draft.physics_models["turbulence_model"] = new_value

    def _apply_change_mesh(
        self, draft: ExperimentDraft, change: dict
    ) -> None:
        new_value = change.get("new_value")
        if new_value:
            draft.mesh.update(new_value)
        else:
            draft.mesh["status"] = "modified"

    def _apply_change_solver(
        self, draft: ExperimentDraft, change: dict
    ) -> None:
        new_value = change.get("new_value")
        if new_value:
            draft.solver.update(new_value)

    def _apply_change_geometry(
        self, draft: ExperimentDraft, change: dict
    ) -> None:
        new_value = change.get("new_value")
        if new_value:
            draft.geometry.update(new_value)

    def _apply_change_numerics(
        self, draft: ExperimentDraft, change: dict
    ) -> None:
        new_value = change.get("new_value")
        if new_value:
            draft.numerics.update(new_value)


__all__ = [
    "ApplyProposalExecutor",
    "ProposalVersionMismatchError",
    "ProposalNotPendingError",
    "UnknownParameterMapper",
]
