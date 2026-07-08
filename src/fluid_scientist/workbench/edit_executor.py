"""Spec edit executor — deterministically applies EditProposal operations.

Takes an EditProposal and a list of accepted operation indices, then
applies each operation to the ExperimentSpec dict.  After applying,
runs the DerivationEngine to propagate changes and the WorkbenchValidator
to check the resulting spec.
"""

from __future__ import annotations

import copy
from typing import Any

from fluid_scientist.workbench.derivation_engine import DerivationEngine
from fluid_scientist.workbench.edit_models import (
    ChangeSummary,
    EditProposal,
    SpecEditOperation,
)
from fluid_scientist.workbench.validator import WorkbenchValidator


class SpecEditExecutor:
    """Deterministically applies EditProposal operations to ExperimentSpec."""

    def __init__(self) -> None:
        self._derivation = DerivationEngine()
        self._validator = WorkbenchValidator()

    def apply(
        self,
        spec: dict[str, Any],
        proposal: EditProposal,
        accepted_operation_indices: list[int],
    ) -> tuple[dict[str, Any], ChangeSummary]:
        """Apply accepted operations and return updated spec + change summary.

        Args:
            spec: The current ExperimentSpec as a dict.
            proposal: The EditProposal to apply.
            accepted_operation_indices: Indices into
                proposal.proposed_operations that the user accepted.

        Returns:
            Tuple of (updated_spec_dict, ChangeSummary).
        """
        # 1. Deep copy spec
        updated_spec = copy.deepcopy(spec)

        direct_updates: list[dict[str, Any]] = []
        added_parameters: list[str] = []
        removed_parameters: list[str] = []
        added_metrics: list[str] = []
        removed_metrics: list[str] = []
        invalidated: list[str] = list(proposal.invalidates)
        changed_param_ids: list[str] = []

        # 2. Apply each accepted operation
        for idx in accepted_operation_indices:
            if idx < 0 or idx >= len(proposal.proposed_operations):
                continue
            op = proposal.proposed_operations[idx]

            result = self._apply_operation(updated_spec, op)
            if result is None:
                continue

            op_type = result["type"]
            if op_type == "direct_update":
                direct_updates.append(result["update"])
                changed_param_ids.append(result["update"]["parameter_id"])
            elif op_type == "added_parameter":
                added_parameters.append(result["parameter_id"])
                changed_param_ids.append(result["parameter_id"])
            elif op_type == "removed_parameter":
                removed_parameters.append(result["parameter_id"])
            elif op_type == "added_metric":
                added_metrics.append(result["metric_id"])
            elif op_type == "removed_metric":
                removed_metrics.append(result["metric_id"])
            elif op_type == "physics_update":
                changed_param_ids.extend(result.get("changed", []))

        # 3. Call DerivationEngine.propagate() for changed parameters
        derived_updates = self._derivation.propagate(
            updated_spec, changed_param_ids
        )

        # Apply derived updates to the spec
        if derived_updates:
            self._apply_derived_updates(updated_spec, derived_updates)

        # 4. Call WorkbenchValidator.validate()
        validation = self._validator.validate(updated_spec)

        # 5. Build ChangeSummary
        # Deduplicate invalidated
        seen_inval: set[str] = set()
        deduped_inval: list[str] = []
        for item in invalidated:
            if item not in seen_inval:
                seen_inval.add(item)
                deduped_inval.append(item)

        # Determine next required action
        next_action = None
        if validation.blocking_issues:
            next_action = "Resolve blocking issues before proceeding"
        elif updated_spec.get("status") == "draft":
            next_action = "Transition to ready for confirmation"
        elif updated_spec.get("status") == "ready":
            next_action = "Confirm the experiment spec"
        elif updated_spec.get("status") == "confirmed":
            next_action = "Compile the experiment case"

        change_summary = ChangeSummary(
            direct_updates=direct_updates,
            derived_updates=derived_updates,
            added_parameters=added_parameters,
            removed_parameters=removed_parameters,
            added_metrics=added_metrics,
            removed_metrics=removed_metrics,
            invalidated=deduped_inval,
            blocking_issues=validation.blocking_issues,
            warnings=validation.warnings,
            next_required_action=next_action,
            can_confirm=validation.can_confirm,
            can_compile=validation.can_compile,
        )

        return updated_spec, change_summary

    def _apply_operation(
        self,
        spec: dict[str, Any],
        op: SpecEditOperation,
    ) -> dict[str, Any] | None:
        """Apply a single operation to the spec dict.

        Returns a result dict describing what was done, or None.
        """
        if op.operation == "add_parameter":
            return self._op_add_parameter(spec, op)
        elif op.operation == "update_parameter":
            return self._op_update_parameter(spec, op)
        elif op.operation == "remove_parameter":
            return self._op_remove_parameter(spec, op)
        elif op.operation == "add_metric":
            return self._op_add_metric(spec, op)
        elif op.operation == "remove_metric":
            return self._op_remove_metric(spec, op)
        elif op.operation == "set_physics":
            return self._op_set_physics(spec, op)
        elif op.operation == "set_boundary_condition":
            return self._op_set_boundary_condition(spec, op)
        elif op.operation == "accept_recommendation":
            return self._op_accept_recommendation(spec, op)
        return None

    @staticmethod
    def _op_add_parameter(
        spec: dict[str, Any],
        op: SpecEditOperation,
    ) -> dict[str, Any]:
        """Add a new parameter to the spec."""
        if op.parameter is None:
            return {"type": "noop"}

        param_dict: dict[str, Any] = {
            "parameter_id": op.parameter.parameter_id,
            "display_name": op.parameter.display_name,
            "category": op.parameter.category,
            "unit": op.parameter.unit,
            "value": op.parameter.value,
            "data_type": "float",
            "source": {
                "type": op.parameter.source,
                "reason": op.parameter.reason,
            },
            "status": "pending",
            "editable": op.parameter.editable,
            "criticality": op.parameter.criticality,
            "dependencies": {
                "depends_on": op.parameter.dependencies,
                "affects": op.parameter.affects,
            },
            "constraints": {},
            "visible_level": "standard",
            "confirmation_policy": "recommend_and_notify",
            "provenance": {"created_by": "user", "source_type": op.parameter.source},
            "validation_rules": [],
        }

        params = spec.setdefault("parameters", [])
        # Check if already exists
        for i, p in enumerate(params):
            if p.get("parameter_id") == op.parameter.parameter_id:
                params[i] = param_dict
                return {
                    "type": "added_parameter",
                    "parameter_id": op.parameter.parameter_id,
                }
        params.append(param_dict)
        return {
            "type": "added_parameter",
            "parameter_id": op.parameter.parameter_id,
        }

    @staticmethod
    def _op_update_parameter(
        spec: dict[str, Any],
        op: SpecEditOperation,
    ) -> dict[str, Any]:
        """Update an existing parameter's value."""
        if op.target_id is None:
            return {"type": "noop"}

        params = spec.get("parameters", [])
        for p in params:
            if p.get("parameter_id") == op.target_id:
                old_value = p.get("value")
                p["value"] = op.value
                if op.unit:
                    p["unit"] = op.unit
                p["status"] = "modified"
                return {
                    "type": "direct_update",
                    "update": {
                        "parameter_id": op.target_id,
                        "old_value": old_value,
                        "new_value": op.value,
                    },
                }
        return {"type": "noop"}

    @staticmethod
    def _op_remove_parameter(
        spec: dict[str, Any],
        op: SpecEditOperation,
    ) -> dict[str, Any]:
        """Remove a parameter from the spec."""
        if op.target_id is None:
            return {"type": "noop"}

        params = spec.get("parameters", [])
        spec["parameters"] = [
            p for p in params
            if p.get("parameter_id") != op.target_id
        ]
        return {
            "type": "removed_parameter",
            "parameter_id": op.target_id,
        }

    @staticmethod
    def _op_add_metric(
        spec: dict[str, Any],
        op: SpecEditOperation,
    ) -> dict[str, Any]:
        """Add a metric to the spec."""
        if op.metric is None:
            return {"type": "noop"}

        metric_dict: dict[str, Any] = {
            "metric_id": op.metric.metric_id,
            "display_name": op.metric.display_name,
            "definition": op.metric.definition,
            "required_data": op.metric.required_data,
            "measurement_requirements": op.metric.measurement_requirements,
            "analysis_pipeline": op.metric.analysis_pipeline,
            "quality_checks": op.metric.quality_checks,
            "reason": op.metric.reason,
        }

        metrics = spec.setdefault("metrics", [])
        # Check if already exists
        for i, m in enumerate(metrics):
            if (
                isinstance(m, dict)
                and m.get("metric_id") == op.metric.metric_id
            ):
                metrics[i] = metric_dict
                return {
                    "type": "added_metric",
                    "metric_id": op.metric.metric_id,
                }
        metrics.append(metric_dict)
        return {
            "type": "added_metric",
            "metric_id": op.metric.metric_id,
        }

    @staticmethod
    def _op_remove_metric(
        spec: dict[str, Any],
        op: SpecEditOperation,
    ) -> dict[str, Any]:
        """Remove a metric from the spec."""
        if op.target_id is None:
            return {"type": "noop"}

        metrics = spec.get("metrics", [])
        spec["metrics"] = [
            m for m in metrics
            if not (
                isinstance(m, dict)
                and m.get("metric_id") == op.target_id
            )
        ]
        return {
            "type": "removed_metric",
            "metric_id": op.target_id,
        }

    @staticmethod
    def _op_set_physics(
        spec: dict[str, Any],
        op: SpecEditOperation,
    ) -> dict[str, Any]:
        """Update physics fields in the spec."""
        physics = spec.setdefault("physics", {})
        changed: list[str] = []
        if op.value is not None and isinstance(op.value, dict):
            for key, val in op.value.items():
                physics[key] = val
                changed.append(key)
        elif op.target_id:
            physics[op.target_id] = op.value
            changed.append(op.target_id)
        return {"type": "physics_update", "changed": changed}

    @staticmethod
    def _op_set_boundary_condition(
        spec: dict[str, Any],
        op: SpecEditOperation,
    ) -> dict[str, Any]:
        """Update boundary condition parameters."""
        if op.target_id is None:
            return {"type": "noop"}

        params = spec.get("parameters", [])
        for p in params:
            if p.get("parameter_id") == op.target_id:
                old_value = p.get("value")
                p["value"] = op.value
                if op.unit:
                    p["unit"] = op.unit
                return {
                    "type": "direct_update",
                    "update": {
                        "parameter_id": op.target_id,
                        "old_value": old_value,
                        "new_value": op.value,
                    },
                }
        return {"type": "noop"}

    @staticmethod
    def _op_accept_recommendation(
        spec: dict[str, Any],
        op: SpecEditOperation,
    ) -> dict[str, Any]:
        """Accept a system_recommended parameter."""
        params = spec.get("parameters", [])
        changed: list[str] = []
        for p in params:
            source = p.get("source", {})
            source_type = (
                source.get("type", "")
                if isinstance(source, dict)
                else str(source)
            )
            if source_type == "system_recommended":
                p["status"] = "accepted"
                changed.append(p.get("parameter_id", ""))
        return {"type": "physics_update", "changed": changed}

    @staticmethod
    def _apply_derived_updates(
        spec: dict[str, Any],
        derived_updates: list[dict[str, Any]],
    ) -> None:
        """Apply derived updates to the spec parameters."""
        params = spec.get("parameters", [])
        for update in derived_updates:
            param_id = update.get("parameter_id")
            for p in params:
                if p.get("parameter_id") == param_id:
                    p["value"] = update.get("new_value")
                    source = p.get("source", {})
                    if isinstance(source, dict):
                        source["type"] = "derived"
                        source["reason"] = update.get("reason", "")
                    p["status"] = "accepted"
                    break


__all__ = ["SpecEditExecutor"]
