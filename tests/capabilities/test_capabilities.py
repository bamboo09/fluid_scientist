"""Tests for the unified MissingCapability and CodeExtension system."""

from __future__ import annotations

import pytest

from fluid_scientist.capabilities.exceptions import (
    BlockingCapabilityError,
    CapabilityError,
    ExtensionApprovalError,
)
from fluid_scientist.capabilities.models import (
    CapabilityRegistry,
    CapabilityType,
    CodeExtensionSpec,
    MissingCapability,
)
from fluid_scientist.capabilities.resolver import (
    CapabilityResolver,
    create_extension_from_capability,
    detect_missing_capabilities_from_metrics,
)
from fluid_scientist.measurement.models import MeasurementPlan
from fluid_scientist.measurement.planner import MetricPlan, UnknownMetric

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_metric_plan(unknown_names: list[str] | None = None) -> MetricPlan:
    """Build a MetricPlan with the given unknown metric names."""
    unknown_names = unknown_names or []
    return MetricPlan(
        unknown_metrics=list(unknown_names),
        unknown_metric_details=[
            UnknownMetric(metric_name=name) for name in unknown_names
        ],
        measurement_plan=MeasurementPlan(),
    )


def make_blocking_capability(
    cap_id: str = "cap_test_1",
    source_module: str = "metric_planner",
) -> MissingCapability:
    return MissingCapability(
        capability_id=cap_id,
        capability_type=CapabilityType.METRIC_OPERATOR,
        requested_behavior="Calculate a custom metric",
        reason="No existing operator can compute this metric",
        severity="blocking",
        code_extension_allowed=True,
        required_inputs=["simulation_data"],
        expected_outputs=["custom_value"],
        suggested_extension_type="metric_operator",
        related_metric_ids=["custom_metric"],
        source_module=source_module,
    )


def make_draft_extension(ext_id: str = "ext_abc123") -> CodeExtensionSpec:
    return CodeExtensionSpec(
        extension_id=ext_id,
        extension_name="Custom metric operator",
        extension_type="metric_operator",
        description="Calculate a custom metric from simulation data",
        rationale="The metric is not available in the registry",
        required_inputs=["simulation_data"],
        expected_outputs=["custom_value"],
        state="draft",
    )


# ---------------------------------------------------------------------------
# 1-2. MissingCapability model
# ---------------------------------------------------------------------------


class TestMissingCapability:
    def test_missing_capability_with_all_fields(self) -> None:
        cap = MissingCapability(
            capability_id="cap_001",
            capability_type=CapabilityType.METRIC_OPERATOR,
            requested_behavior="Calculate Nusselt number",
            reason="No built-in operator for Nusselt number",
            severity="blocking",
            code_extension_allowed=True,
            required_inputs=["temperature_field", "velocity_field"],
            expected_outputs=["nusselt_number"],
            suggested_extension_type="metric_operator",
            related_metric_ids=["nusselt_number"],
            related_parameter_ids=["reynolds_number"],
            source_module="metric_planner",
        )
        assert cap.capability_id == "cap_001"
        assert cap.capability_type == "metric_operator"
        assert cap.requested_behavior == "Calculate Nusselt number"
        assert cap.severity == "blocking"
        assert cap.code_extension_allowed is True
        assert cap.required_inputs == ["temperature_field", "velocity_field"]
        assert cap.expected_outputs == ["nusselt_number"]
        assert cap.suggested_extension_type == "metric_operator"
        assert cap.related_metric_ids == ["nusselt_number"]
        assert cap.related_parameter_ids == ["reynolds_number"]
        assert cap.source_module == "metric_planner"

    def test_is_blocking_returns_true_for_blocking_severity(self) -> None:
        cap = MissingCapability(
            capability_id="cap_blocking",
            capability_type=CapabilityType.METRIC_OPERATOR,
            requested_behavior="test",
            reason="test",
            severity="blocking",
        )
        assert cap.is_blocking() is True

    def test_is_blocking_returns_false_for_warning_severity(self) -> None:
        cap = MissingCapability(
            capability_id="cap_warning",
            capability_type=CapabilityType.METRIC_OPERATOR,
            requested_behavior="test",
            reason="test",
            severity="warning",
        )
        assert cap.is_blocking() is False

    def test_default_severity_is_blocking(self) -> None:
        cap = MissingCapability(
            capability_id="cap_default",
            capability_type=CapabilityType.ANALYSIS_PLUGIN,
            requested_behavior="test",
            reason="test",
        )
        # Unified model defaults to "blocking" (v5 default, fail-closed).
        assert cap.severity == "blocking"
        assert cap.is_blocking() is True

    def test_source_module_field(self) -> None:
        """Test 15: MissingCapability has correct source_module field."""
        cap = MissingCapability(
            capability_id="cap_src",
            capability_type=CapabilityType.SOLVER_EXTENSION,
            requested_behavior="Custom solver",
            reason="Need custom solver",
            source_module="solver_capability_resolver",
        )
        assert cap.source_module == "solver_capability_resolver"


# ---------------------------------------------------------------------------
# 3-5. CodeExtensionSpec lifecycle
# ---------------------------------------------------------------------------


class TestCodeExtensionLifecycle:
    def test_full_lifecycle_draft_to_registered(self) -> None:
        """Test 3: draft → sandbox_tested → auto_tested → approved → registered."""
        ext = make_draft_extension()
        assert ext.state == "draft"

        ext = ext.transition_to("sandbox_tested")
        assert ext.state == "sandbox_tested"

        ext = ext.transition_to("auto_tested")
        assert ext.state == "auto_tested"

        ext = ext.transition_to("approved")
        assert ext.state == "approved"
        assert ext.approved_by == "expert"
        assert ext.approved_at is not None

        ext = ext.transition_to("registered")
        assert ext.state == "registered"

    def test_transition_to_raises_for_invalid_transition(self) -> None:
        """Test 4: transition_to() raises ValueError for invalid transitions."""
        ext = make_draft_extension()
        # draft → auto_tested is not allowed (must go through sandbox_tested first)
        with pytest.raises(ValueError, match="Invalid transition"):
            ext.transition_to("auto_tested")

    def test_transition_to_raises_from_terminal_state(self) -> None:
        ext = make_draft_extension()
        ext = ext.transition_to("sandbox_tested")
        ext = ext.transition_to("auto_tested")
        ext = ext.transition_to("approved")
        ext = ext.transition_to("registered")
        # registered is terminal
        with pytest.raises(ValueError, match="Invalid transition"):
            ext.transition_to("draft")

    def test_can_transition_to_returns_true_for_valid(self) -> None:
        """Test 5: can_transition_to() returns correct boolean."""
        ext = make_draft_extension()
        assert ext.can_transition_to("sandbox_tested") is True
        assert ext.can_transition_to("rejected") is True

    def test_can_transition_to_returns_false_for_invalid(self) -> None:
        ext = make_draft_extension()
        assert ext.can_transition_to("auto_tested") is False
        assert ext.can_transition_to("registered") is False
        assert ext.can_transition_to("approved") is False

    def test_transition_to_with_comment(self) -> None:
        ext = make_draft_extension()
        ext = ext.transition_to("sandbox_tested", comment="Sandbox tests passed")
        assert ext.approval_comment == "Sandbox tests passed"

    def test_conditionally_approved_lifecycle(self) -> None:
        ext = make_draft_extension()
        ext = ext.transition_to("sandbox_tested")
        ext = ext.transition_to("auto_tested")
        ext = ext.transition_to("conditionally_approved")
        assert ext.state == "conditionally_approved"
        ext = ext.transition_to("registered")
        assert ext.state == "registered"

    def test_rejected_is_terminal(self) -> None:
        ext = make_draft_extension()
        ext = ext.transition_to("rejected")
        assert ext.state == "rejected"
        assert ext.can_transition_to("draft") is False


# ---------------------------------------------------------------------------
# 6-7. CapabilityRegistry
# ---------------------------------------------------------------------------


class TestCapabilityRegistry:
    def test_register_raises_for_non_approved_extension(self) -> None:
        """Test 6: register() raises ValueError for non-approved extension."""
        registry = CapabilityRegistry()
        ext = make_draft_extension()  # state=draft
        with pytest.raises(ValueError, match="Cannot register extension"):
            registry.register(ext)

    def test_has_capability_returns_true_after_registration(self) -> None:
        """Test 7: has_capability() returns True after registration."""
        registry = CapabilityRegistry()
        ext = make_draft_extension()
        ext = ext.transition_to("sandbox_tested")
        ext = ext.transition_to("auto_tested")
        ext = ext.transition_to("approved")
        registry.register(ext)
        assert registry.has_capability(ext.extension_id) is True

    def test_has_capability_returns_false_before_registration(self) -> None:
        registry = CapabilityRegistry()
        assert registry.has_capability("nonexistent") is False

    def test_get_capability_returns_data_after_registration(self) -> None:
        registry = CapabilityRegistry()
        ext = make_draft_extension("ext_xyz")
        ext = ext.transition_to("sandbox_tested")
        ext = ext.transition_to("auto_tested")
        ext = ext.transition_to("approved")
        registry.register(ext)
        data = registry.get_capability("ext_xyz")
        assert data is not None
        assert data["extension_id"] == "ext_xyz"
        assert data["extension_type"] == "metric_operator"

    def test_list_capabilities_returns_all(self) -> None:
        registry = CapabilityRegistry()
        assert registry.list_capabilities() == []

        ext = make_draft_extension("ext_1")
        ext = ext.transition_to("sandbox_tested")
        ext = ext.transition_to("auto_tested")
        ext = ext.transition_to("approved")
        registry.register(ext)

        assert len(registry.list_capabilities()) == 1


# ---------------------------------------------------------------------------
# 8, 14. detect_missing_capabilities_from_metrics
# ---------------------------------------------------------------------------


class TestDetectMissingCapabilities:
    def test_creates_blocking_capability_for_unknown_metric(self) -> None:
        """Test 8: creates blocking capability for unknown metric."""
        plan = make_metric_plan(["custom_metric"])
        caps = detect_missing_capabilities_from_metrics(plan)
        assert len(caps) == 1
        cap = caps[0]
        assert cap.capability_type == CapabilityType.METRIC_OPERATOR
        assert cap.severity == "blocking"
        assert cap.is_blocking() is True
        assert "custom_metric" in cap.capability_id
        assert "custom_metric" in cap.requested_behavior
        assert "custom_metric" in cap.reason

    def test_multiple_unknown_metrics_generate_separate_capabilities(self) -> None:
        """Test 14: each unknown metric generates a separate MissingCapability."""
        plan = make_metric_plan(["metric_a", "metric_b", "metric_c"])
        caps = detect_missing_capabilities_from_metrics(plan)
        assert len(caps) == 3
        cap_ids = [c.capability_id for c in caps]
        assert len(set(cap_ids)) == 3  # all unique
        # Each should reference its own metric
        for cap, expected_name in zip(caps, ["metric_a", "metric_b", "metric_c"], strict=False):
            assert expected_name in cap.capability_id
            assert cap.related_metric_ids == [expected_name]

    def test_no_unknown_metrics_returns_empty(self) -> None:
        plan = make_metric_plan([])
        caps = detect_missing_capabilities_from_metrics(plan)
        assert caps == []

    def test_detected_capability_has_correct_source_module(self) -> None:
        """Test 15 (also): source_module is set to 'metric_planner'."""
        plan = make_metric_plan(["some_metric"])
        caps = detect_missing_capabilities_from_metrics(plan)
        assert caps[0].source_module == "metric_planner"

    def test_detected_capability_has_required_inputs_and_outputs(self) -> None:
        plan = make_metric_plan(["nusselt_number"])
        caps = detect_missing_capabilities_from_metrics(plan)
        cap = caps[0]
        assert cap.required_inputs == ["simulation_data"]
        assert cap.expected_outputs == ["nusselt_number_value"]


# ---------------------------------------------------------------------------
# 9-12. CapabilityResolver
# ---------------------------------------------------------------------------


class TestCapabilityResolver:
    def test_resolve_returns_empty_when_no_unknown_metrics(self) -> None:
        """Test 9: resolve() returns empty list when no unknown metrics."""
        resolver = CapabilityResolver()
        plan = make_metric_plan([])
        result = resolver.resolve(metric_plan=plan)
        assert result == []

    def test_resolve_returns_capabilities_for_unknown_metrics(self) -> None:
        resolver = CapabilityResolver()
        plan = make_metric_plan(["custom_metric"])
        result = resolver.resolve(metric_plan=plan)
        assert len(result) == 1
        assert result[0].is_blocking() is True

    def test_create_extensions_creates_code_extension_spec(self) -> None:
        """Test 10: create_extensions() creates CodeExtensionSpec from MissingCapability."""
        resolver = CapabilityResolver()
        cap = make_blocking_capability()
        extensions = resolver.create_extensions([cap])
        assert len(extensions) == 1
        ext = extensions[0]
        assert isinstance(ext, CodeExtensionSpec)
        assert ext.state == "draft"
        assert ext.extension_type == "metric_operator"
        assert ext.related_capability_id == cap.capability_id
        assert ext.rationale == cap.reason
        assert ext.required_inputs == cap.required_inputs

    def test_create_extensions_skips_non_allowed(self) -> None:
        resolver = CapabilityResolver()
        cap = MissingCapability(
            capability_id="cap_no_ext",
            capability_type=CapabilityType.METRIC_OPERATOR,
            requested_behavior="test",
            reason="test",
            code_extension_allowed=False,
        )
        extensions = resolver.create_extensions([cap])
        assert len(extensions) == 0

    def test_approve_and_register_full_lifecycle(self) -> None:
        """Test 11: approve_and_register() transitions through full lifecycle."""
        resolver = CapabilityResolver()
        cap = make_blocking_capability()
        extensions = resolver.create_extensions([cap])
        ext = extensions[0]
        assert ext.state == "draft"

        result = resolver.approve_and_register(ext, decision="approved")
        assert result.state == "registered"
        assert resolver.registry.has_capability(result.extension_id) is True

    def test_approve_and_register_conditionally_approved(self) -> None:
        resolver = CapabilityResolver()
        cap = make_blocking_capability()
        ext = resolver.create_extensions([cap])[0]
        result = resolver.approve_and_register(
            ext, decision="conditionally_approved", comment="Minor issues"
        )
        assert result.state == "registered"
        assert resolver.registry.has_capability(result.extension_id) is True

    def test_approve_and_register_rejected(self) -> None:
        resolver = CapabilityResolver()
        cap = make_blocking_capability()
        ext = resolver.create_extensions([cap])[0]
        result = resolver.approve_and_register(ext, decision="rejected")
        assert result.state == "rejected"
        assert not resolver.registry.has_capability(result.extension_id)

    def test_approve_and_register_invalid_decision(self) -> None:
        resolver = CapabilityResolver()
        ext = make_draft_extension()
        with pytest.raises(ValueError, match="Invalid decision"):
            resolver.approve_and_register(ext, decision="bogus")

    def test_resolve_no_longer_returns_registered_capability(self) -> None:
        """Test 12: after registration, resolve() no longer returns the capability."""
        resolver = CapabilityResolver()
        plan = make_metric_plan(["custom_metric"])
        # Before registration, the capability is detected
        caps_before = resolver.resolve(metric_plan=plan)
        assert len(caps_before) == 1

        # Register the extension to satisfy the capability
        ext = resolver.create_extensions(caps_before)[0]
        registered = resolver.approve_and_register(ext, decision="approved")
        assert registered.state == "registered"

        # After registration, the capability_id is in the registry.
        # The resolver filters out registered capabilities, so resolve()
        # should return an empty list.
        caps_after = resolver.resolve(metric_plan=plan)
        assert len(caps_after) == 0

    def test_resolve_filters_registered_capabilities(self) -> None:
        """Verify that resolve() uses the registry to filter."""
        resolver = CapabilityResolver()
        plan_with_two = make_metric_plan(["metric_a", "metric_b"])
        caps = resolver.resolve(metric_plan=plan_with_two)
        assert len(caps) == 2

        # Approve and register only one
        ext = resolver.create_extensions([caps[0]])[0]
        resolver.approve_and_register(ext, decision="approved")

        # Only the unregistered one should remain
        remaining = resolver.resolve(metric_plan=plan_with_two)
        assert len(remaining) == 1
        assert remaining[0].capability_id == caps[1].capability_id


# ---------------------------------------------------------------------------
# 13. BlockingCapabilityError
# ---------------------------------------------------------------------------


class TestBlockingCapabilityError:
    def test_blocking_capability_error_raised_when_blocking_caps_exist(self) -> None:
        """Test 13: BlockingCapabilityError is raised, not silently swallowed."""
        resolver = CapabilityResolver()
        plan = make_metric_plan(["blocking_metric"])
        with pytest.raises(BlockingCapabilityError) as exc_info:
            resolver.resolve_or_raise(metric_plan=plan)
        # The error must carry the blocking capabilities
        assert len(exc_info.value.capabilities) == 1
        assert exc_info.value.capabilities[0].is_blocking() is True
        assert "blocking_metric" in exc_info.value.capabilities[0].capability_id

    def test_resolve_or_raise_does_not_raise_when_no_blocking(self) -> None:
        resolver = CapabilityResolver()
        plan = make_metric_plan([])  # no unknown metrics
        # Should not raise — returns empty list
        result = resolver.resolve_or_raise(metric_plan=plan)
        assert result == []

    def test_blocking_capability_error_is_capability_error(self) -> None:
        assert issubclass(BlockingCapabilityError, CapabilityError)
        assert issubclass(ExtensionApprovalError, CapabilityError)

    def test_blocking_capability_error_carries_capabilities(self) -> None:
        cap = make_blocking_capability()
        err = BlockingCapabilityError("test message", capabilities=[cap])
        assert err.capabilities == [cap]
        assert str(err) == "test message"

    def test_blocking_capability_error_default_capabilities_empty(self) -> None:
        err = BlockingCapabilityError("test")
        assert err.capabilities == []


# ---------------------------------------------------------------------------
# create_extension_from_capability standalone function
# ---------------------------------------------------------------------------


class TestCreateExtensionFromCapability:
    def test_creates_extension_with_correct_fields(self) -> None:
        cap = make_blocking_capability("cap_func_test")
        ext = create_extension_from_capability(
            cap,
            research_session_id="session_1",
            experiment_spec_id="exp_1",
        )
        assert ext.extension_id.startswith("ext_")
        assert ext.extension_name == cap.requested_behavior
        assert ext.extension_type == "metric_operator"
        assert ext.description == cap.requested_behavior
        assert ext.rationale == cap.reason
        assert ext.related_capability_id == "cap_func_test"
        assert ext.research_session_id == "session_1"
        assert ext.experiment_spec_id == "exp_1"
        assert ext.state == "draft"

    def test_uses_default_extension_type_when_none(self) -> None:
        cap = MissingCapability(
            capability_id="cap_no_type",
            capability_type=CapabilityType.ANALYSIS_PLUGIN,
            requested_behavior="test",
            reason="test",
            suggested_extension_type=None,
        )
        ext = create_extension_from_capability(cap)
        assert ext.extension_type == "analysis_plugin"
