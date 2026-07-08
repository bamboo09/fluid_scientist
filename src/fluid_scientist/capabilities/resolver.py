"""Capability resolver — detects missing capabilities and manages extensions."""

from __future__ import annotations

from uuid import uuid4

from fluid_scientist.capabilities.exceptions import BlockingCapabilityError
from fluid_scientist.capabilities.models import (
    CapabilityRegistry,
    CapabilityType,
    CodeExtensionSpec,
    MissingCapability,
)
from fluid_scientist.measurement.planner import MetricPlan


def detect_missing_capabilities_from_metrics(
    metric_plan: MetricPlan,
) -> list[MissingCapability]:
    """Detect missing capabilities from unknown metrics in a MetricPlan.

    Each unknown metric that has no registry match generates a
    blocking MissingCapability with capability_type=metric_operator.
    """
    capabilities: list[MissingCapability] = []

    for unknown in metric_plan.unknown_metric_details:
        cap = MissingCapability(
            capability_id=f"cap_metric_{unknown.metric_name}",
            capability_type=CapabilityType.METRIC_OPERATOR,
            requested_behavior=f"Calculate metric: {unknown.metric_name}",
            reason=(
                f"Metric '{unknown.metric_name}' is not in the metric registry "
                f"and cannot be calculated by existing operators"
            ),
            severity="blocking",
            code_extension_allowed=True,
            required_inputs=["simulation_data"],
            expected_outputs=[f"{unknown.metric_name}_value"],
            suggested_extension_type="metric_operator",
            related_metric_ids=[unknown.metric_name],
            source_module="metric_planner",
        )
        capabilities.append(cap)

    return capabilities


def create_extension_from_capability(
    capability: MissingCapability,
    research_session_id: str | None = None,
    experiment_spec_id: str | None = None,
) -> CodeExtensionSpec:
    """Create a CodeExtensionSpec from a MissingCapability."""
    return CodeExtensionSpec(
        extension_id=f"ext_{uuid4().hex[:12]}",
        extension_name=capability.requested_behavior,
        extension_type=capability.suggested_extension_type or "analysis_plugin",
        description=capability.requested_behavior,
        rationale=capability.reason,
        required_inputs=capability.required_inputs,
        expected_outputs=capability.expected_outputs,
        related_capability_id=capability.capability_id,
        research_session_id=research_session_id,
        experiment_spec_id=experiment_spec_id,
        state="draft",
    )


class CapabilityResolver:
    """Resolves missing capabilities and manages the extension lifecycle.

    Flow:
    1. Detect missing capabilities from various sources
    2. Create CodeExtensionSpec for each blocking capability
    3. After extension approval + registration, re-validate spec
    """

    def __init__(self, registry: CapabilityRegistry | None = None) -> None:
        self._registry = registry or CapabilityRegistry()

    def resolve(
        self,
        metric_plan: MetricPlan | None = None,
        experiment_spec_id: str | None = None,
        research_session_id: str | None = None,
    ) -> list[MissingCapability]:
        """Detect all missing capabilities from available sources."""
        capabilities: list[MissingCapability] = []

        if metric_plan is not None:
            capabilities.extend(
                detect_missing_capabilities_from_metrics(metric_plan)
            )

        # Filter out capabilities that are already registered
        unmet = [
            cap
            for cap in capabilities
            if not self._registry.has_capability(cap.capability_id)
        ]

        return unmet

    def resolve_or_raise(
        self,
        metric_plan: MetricPlan | None = None,
        experiment_spec_id: str | None = None,
        research_session_id: str | None = None,
    ) -> list[MissingCapability]:
        """Resolve missing capabilities, raising if any are blocking.

        Unlike :meth:`resolve`, this method raises
        :class:`BlockingCapabilityError` when blocking capabilities are
        detected, ensuring they are not silently swallowed.
        """
        capabilities = self.resolve(
            metric_plan=metric_plan,
            experiment_spec_id=experiment_spec_id,
            research_session_id=research_session_id,
        )
        blocking = [cap for cap in capabilities if cap.is_blocking()]
        if blocking:
            raise BlockingCapabilityError(
                f"{len(blocking)} blocking capability/capabilities detected",
                capabilities=blocking,
            )
        return capabilities

    def create_extensions(
        self,
        capabilities: list[MissingCapability],
        research_session_id: str | None = None,
        experiment_spec_id: str | None = None,
    ) -> list[CodeExtensionSpec]:
        """Create CodeExtensionSpecs for missing capabilities."""
        return [
            create_extension_from_capability(
                cap,
                research_session_id=research_session_id,
                experiment_spec_id=experiment_spec_id,
            )
            for cap in capabilities
            if cap.code_extension_allowed
        ]

    def approve_and_register(
        self,
        extension: CodeExtensionSpec,
        decision: str = "approved",
        comment: str | None = None,
    ) -> CodeExtensionSpec:
        """Process approval decision and register if approved.

        Args:
            extension: The extension to approve
            decision: One of approved, conditionally_approved, rejected, revision_required
            comment: Optional approval comment

        Returns:
            Updated CodeExtensionSpec
        """
        if decision not in (
            "approved",
            "conditionally_approved",
            "rejected",
            "revision_required",
        ):
            raise ValueError(f"Invalid decision: {decision}")

        # Transition through the lifecycle
        updated = extension

        if updated.state == "draft":
            updated = updated.transition_to("sandbox_tested", comment)
        if updated.state == "sandbox_tested":
            updated = updated.transition_to("auto_tested", comment)
        if updated.state == "auto_tested":
            updated = updated.transition_to(decision, comment)

        if decision in ("approved", "conditionally_approved"):
            updated = updated.transition_to("registered", comment)
            self._registry.register(updated)

        return updated

    @property
    def registry(self) -> CapabilityRegistry:
        return self._registry


__all__ = [
    "CapabilityResolver",
    "create_extension_from_capability",
    "detect_missing_capabilities_from_metrics",
]
