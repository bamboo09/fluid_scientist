"""Tests for MetricPlanner V2 — metric planner restructure (Commit 4).

Verifies the new planning pipeline:
  research problem -> physical quantity decomposition -> metric candidates
  -> core/credibility/comparison/optional metrics -> required data
  -> MeasurementPlan.

Also verifies unknown metric extraction from natural language and metric
definition conflict handling.
"""

from __future__ import annotations

import pytest

from fluid_scientist.measurement.models import MeasurementPlan
from fluid_scientist.measurement.planner import (
    MetricPlan,
    MetricPlanner,
    UnknownMetric,
)
from fluid_scientist.research.models import ResearchPhysicsSpec


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def planner() -> MetricPlanner:
    return MetricPlanner()


@pytest.fixture
def physics_spec_with_flow_regime() -> ResearchPhysicsSpec:
    return ResearchPhysicsSpec(
        flow_regime="laminar",
        geometry_facts={"diameter": 0.05},
        material_facts={"fluid_type": "water"},
    )


# ---------------------------------------------------------------------------
# 1. UnknownMetric model
# ---------------------------------------------------------------------------


class TestUnknownMetricModel:
    def test_unknown_metric_required_fields(self):
        """UnknownMetric can be created with just metric_name."""
        um = UnknownMetric(metric_name="custom_metric")
        assert um.metric_name == "custom_metric"
        assert um.registry_match is None
        assert um.status == "unknown"
        assert um.user_requested is True
        assert um.extraction_source is None

    def test_unknown_metric_all_fields(self):
        """UnknownMetric accepts all fields."""
        um = UnknownMetric(
            metric_name="vortex_breakdown_index",
            registry_match=None,
            status="awaiting_code_approval",
            user_requested=False,
            extraction_source="user message about vortex breakdown",
        )
        assert um.metric_name == "vortex_breakdown_index"
        assert um.status == "awaiting_code_approval"
        assert um.user_requested is False
        assert um.extraction_source == "user message about vortex breakdown"

    def test_unknown_metric_status_validation(self):
        """UnknownMetric status must be one of the allowed literals."""
        um = UnknownMetric(metric_name="m", status="pending_lookup")
        assert um.status == "pending_lookup"

        with pytest.raises(ValueError):
            UnknownMetric(metric_name="m", status="invalid_status")


# ---------------------------------------------------------------------------
# 2. MetricPlan new fields
# ---------------------------------------------------------------------------


class TestMetricPlanNewFields:
    def test_metric_plan_has_new_fields(self):
        """MetricPlan has comparison_metrics, optional_metrics,
        unknown_metric_details, metric_definitions, reasoning_summary."""
        plan = MetricPlan(measurement_plan=MeasurementPlan())
        assert hasattr(plan, "comparison_metrics")
        assert hasattr(plan, "optional_metrics")
        assert hasattr(plan, "unknown_metric_details")
        assert hasattr(plan, "metric_definitions")
        assert hasattr(plan, "reasoning_summary")

    def test_metric_plan_default_values(self):
        """New fields have correct default values."""
        plan = MetricPlan(measurement_plan=MeasurementPlan())
        assert plan.comparison_metrics == []
        assert plan.optional_metrics == []
        assert plan.unknown_metric_details == []
        assert plan.metric_definitions == {}
        assert plan.reasoning_summary == ""


# ---------------------------------------------------------------------------
# 3. Metric classification
# ---------------------------------------------------------------------------


class TestMetricClassification:
    def test_metrics_classified_into_categories(self, planner: MetricPlanner):
        """MetricPlanner.propose_metrics() classifies metrics into
        core/credibility/comparison/extension categories."""
        plan = planner.propose_metrics(
            research_objective="研究圆柱绕流阻力",
            user_metrics=["drag_coefficient"],
            experiment_type="cylinder_flow",
        )

        # drag_coefficient is critical + user-requested -> core
        assert "drag_coefficient" in plan.core_metrics

        # residual_tolerance is convergence -> credibility
        assert "residual_tolerance" in plan.credibility_metrics

        # lift_coefficient / strouhal_number / pressure_drop are extension
        assert "lift_coefficient" in plan.extension_metrics
        assert "strouhal_number" in plan.extension_metrics

    def test_categorization_no_registry_match(self, planner: MetricPlanner):
        """No registry match: user metrics go to core, rest is empty."""
        plan = planner.propose_metrics(
            research_objective="研究",
            user_metrics=["pressure_drop"],
            experiment_type="unknown_type",
        )

        assert "pressure_drop" in plan.core_metrics
        assert plan.credibility_metrics == []
        assert plan.extension_metrics == []


# ---------------------------------------------------------------------------
# 4. Unknown metric extraction
# ---------------------------------------------------------------------------


class TestUnknownMetricExtraction:
    def test_unknown_metric_extracted_as_unknown_metric(
        self, planner: MetricPlanner
    ):
        """Unknown metric like '旋涡破碎指数' is extracted as UnknownMetric
        with status='unknown'."""
        plan = planner.propose_metrics(
            research_objective="研究旋涡破碎指数",
            user_metrics=["vortex_breakdown_index"],
            experiment_type="laminar_pipe",
        )

        assert "vortex_breakdown_index" in plan.unknown_metrics
        # Should also be in unknown_metric_details
        details = plan.unknown_metric_details
        assert len(details) == 1
        assert details[0].metric_name == "vortex_breakdown_index"
        assert details[0].status == "unknown"
        assert details[0].user_requested is True

    def test_unknown_metric_extraction_source(
        self, planner: MetricPlanner
    ):
        """UnknownMetric.extraction_source is set from research_objective."""
        objective = "研究弯管后旋涡破碎指数和压降"
        plan = planner.propose_metrics(
            research_objective=objective,
            user_metrics=["vortex_breakdown_index"],
            experiment_type="laminar_pipe",
        )

        details = plan.unknown_metric_details
        assert len(details) == 1
        assert details[0].extraction_source is not None
        assert "旋涡破碎指数" in details[0].extraction_source


# ---------------------------------------------------------------------------
# 5. metric_definitions
# ---------------------------------------------------------------------------


class TestMetricDefinitions:
    def test_metric_definitions_contain_formula_unit_category(
        self, planner: MetricPlanner
    ):
        """metric_definitions contains formula, unit, category for each
        known metric."""
        plan = planner.propose_metrics(
            research_objective="研究圆柱绕流阻力",
            user_metrics=["drag_coefficient"],
            experiment_type="cylinder_flow",
        )

        defs = plan.metric_definitions
        # drag_coefficient should have a definition
        assert "drag_coefficient" in defs
        d = defs["drag_coefficient"]
        assert "formula" in d
        assert "unit" in d
        assert "category" in d
        assert d["unit"] == "dimensionless"

        # residual_tolerance should also have a definition
        assert "residual_tolerance" in defs
        rd = defs["residual_tolerance"]
        assert "formula" in rd
        assert "unit" in rd
        assert "category" in rd

    def test_metric_definitions_for_user_metric_not_in_registry(
        self, planner: MetricPlanner
    ):
        """A known standard metric requested by user but not in the registry
        still gets a definition from _METRIC_DEFINITIONS."""
        plan = planner.propose_metrics(
            research_objective="研究阻力",
            user_metrics=["drag_coefficient"],
            experiment_type="laminar_pipe",  # pipe registry has no drag_coefficient
        )

        defs = plan.metric_definitions
        assert "drag_coefficient" in defs
        assert "formula" in defs["drag_coefficient"]
        assert defs["drag_coefficient"]["unit"] == "dimensionless"


# ---------------------------------------------------------------------------
# 6. reasoning_summary
# ---------------------------------------------------------------------------


class TestReasoningSummary:
    def test_reasoning_summary_non_empty(self, planner: MetricPlanner):
        """reasoning_summary is non-empty and mentions metric categories."""
        plan = planner.propose_metrics(
            research_objective="研究圆柱绕流阻力",
            user_metrics=["drag_coefficient"],
            experiment_type="cylinder_flow",
        )

        assert plan.reasoning_summary != ""
        # Should mention at least one category
        assert "核心指标" in plan.reasoning_summary or "可信度指标" in plan.reasoning_summary

    def test_reasoning_summary_mentions_all_present_categories(
        self, planner: MetricPlanner
    ):
        """reasoning_summary mentions each non-empty category."""
        plan = planner.propose_metrics(
            research_objective="研究圆柱绕流",
            user_metrics=["drag_coefficient"],
            experiment_type="cylinder_flow",
        )

        summary = plan.reasoning_summary
        if plan.core_metrics:
            assert "核心指标" in summary
        if plan.credibility_metrics:
            assert "可信度指标" in summary
        if plan.extension_metrics:
            assert "扩展指标" in summary

    def test_reasoning_summary_mentions_unknown_metrics(
        self, planner: MetricPlanner
    ):
        """reasoning_summary mentions unknown metrics when present."""
        plan = planner.propose_metrics(
            research_objective="研究未知指标",
            user_metrics=["totally_unknown"],
            experiment_type="cylinder_flow",
        )

        assert "未知指标" in plan.reasoning_summary


# ---------------------------------------------------------------------------
# 7. Comparison metrics with physics_spec
# ---------------------------------------------------------------------------


class TestComparisonMetrics:
    def test_reynolds_number_added_when_flow_regime_present(
        self,
        planner: MetricPlanner,
        physics_spec_with_flow_regime: ResearchPhysicsSpec,
    ):
        """reynolds_number is added to comparison_metrics when physics_spec
        has flow_regime."""
        plan = planner.propose_metrics(
            research_objective="研究管内流动",
            physics_spec=physics_spec_with_flow_regime,
            user_metrics=["pressure_drop"],
            experiment_type="lid_driven_cavity",  # cavity registry has no reynolds_number
        )

        # reynolds_number should be in comparison_metrics because flow_regime
        # is set and reynolds_number is not already in the registry
        assert "reynolds_number" in plan.comparison_metrics

    def test_reynolds_number_not_added_without_flow_regime(
        self, planner: MetricPlanner
    ):
        """reynolds_number is NOT added when physics_spec has no flow_regime."""
        plan = planner.propose_metrics(
            research_objective="研究管内流动",
            physics_spec=None,
            user_metrics=["pressure_drop"],
            experiment_type="lid_driven_cavity",
        )

        # Without flow_regime, reynolds_number should not be auto-added
        assert "reynolds_number" not in plan.comparison_metrics

    def test_reynolds_number_in_registry_goes_to_comparison(
        self,
        planner: MetricPlanner,
        physics_spec_with_flow_regime: ResearchPhysicsSpec,
    ):
        """reynolds_number already in registry (pipe) is classified as
        comparison (not core or extension)."""
        plan = planner.propose_metrics(
            research_objective="研究管内流动",
            physics_spec=physics_spec_with_flow_regime,
            user_metrics=["pressure_drop"],
            experiment_type="laminar_pipe",
        )

        # reynolds_number is in pipe registry and is in _COMPARISON_METRICS
        assert "reynolds_number" in plan.comparison_metrics


# ---------------------------------------------------------------------------
# 8. Known metric from user but not in registry -> core
# ---------------------------------------------------------------------------


class TestKnownMetricNotInRegistry:
    def test_known_user_metric_goes_to_core(self, planner: MetricPlanner):
        """A known standard metric from user that is not in the registry
        still goes to core (not unknown)."""
        plan = planner.propose_metrics(
            research_objective="研究阻力",
            user_metrics=["drag_coefficient"],
            experiment_type="laminar_pipe",  # pipe registry has no drag_coefficient
        )

        assert "drag_coefficient" in plan.core_metrics
        assert "drag_coefficient" not in plan.unknown_metrics
        assert "drag_coefficient" not in plan.unknown_metric_details

    def test_multiple_known_user_metrics_go_to_core(
        self, planner: MetricPlanner
    ):
        """Multiple known standard metrics from user go to core."""
        plan = planner.propose_metrics(
            research_objective="研究多种指标",
            user_metrics=["drag_coefficient", "lift_coefficient", "strouhal_number"],
            experiment_type="laminar_pipe",
        )

        assert "drag_coefficient" in plan.core_metrics
        assert "lift_coefficient" in plan.core_metrics
        assert "strouhal_number" in plan.core_metrics
        assert len(plan.unknown_metrics) == 0


# ---------------------------------------------------------------------------
# 9. Multiple unknown metrics
# ---------------------------------------------------------------------------


class TestMultipleUnknownMetrics:
    def test_multiple_unknown_metrics_captured(self, planner: MetricPlanner):
        """Multiple unknown metrics are all captured."""
        plan = planner.propose_metrics(
            research_objective="研究多种未知指标",
            user_metrics=["custom_metric_1", "custom_metric_2", "custom_metric_3"],
            experiment_type="laminar_pipe",
        )

        assert len(plan.unknown_metrics) == 3
        assert set(plan.unknown_metrics) == {
            "custom_metric_1",
            "custom_metric_2",
            "custom_metric_3",
        }

        details = plan.unknown_metric_details
        assert len(details) == 3
        detail_names = {d.metric_name for d in details}
        assert detail_names == {
            "custom_metric_1",
            "custom_metric_2",
            "custom_metric_3",
        }
        for d in details:
            assert d.status == "unknown"

    def test_mixed_known_and_unknown_metrics(self, planner: MetricPlanner):
        """Both known and unknown metrics are handled correctly."""
        plan = planner.propose_metrics(
            research_objective="研究已知和未知指标",
            user_metrics=["pressure_drop", "unknown_metric_x"],
            experiment_type="laminar_pipe",
        )

        assert "pressure_drop" in plan.core_metrics
        assert "unknown_metric_x" in plan.unknown_metrics
        assert len(plan.unknown_metric_details) == 1
        assert plan.unknown_metric_details[0].metric_name == "unknown_metric_x"


# ---------------------------------------------------------------------------
# 10. Backward compatibility
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:
    def test_core_metrics_is_list_of_strings(self, planner: MetricPlanner):
        """core_metrics is still a list of strings."""
        plan = planner.propose_metrics(
            research_objective="研究",
            user_metrics=["pressure_drop"],
            experiment_type="laminar_pipe",
        )

        assert isinstance(plan.core_metrics, list)
        for m in plan.core_metrics:
            assert isinstance(m, str)

    def test_credibility_metrics_is_list_of_strings(
        self, planner: MetricPlanner
    ):
        """credibility_metrics is still a list of strings."""
        plan = planner.propose_metrics(
            research_objective="研究",
            user_metrics=["pressure_drop"],
            experiment_type="laminar_pipe",
        )

        assert isinstance(plan.credibility_metrics, list)
        for m in plan.credibility_metrics:
            assert isinstance(m, str)

    def test_extension_metrics_is_list_of_strings(
        self, planner: MetricPlanner
    ):
        """extension_metrics is still a list of strings."""
        plan = planner.propose_metrics(
            research_objective="研究",
            user_metrics=["pressure_drop"],
            experiment_type="laminar_pipe",
        )

        assert isinstance(plan.extension_metrics, list)
        for m in plan.extension_metrics:
            assert isinstance(m, str)

    def test_unknown_metrics_is_list_of_strings(self, planner: MetricPlanner):
        """unknown_metrics is still a list of strings."""
        plan = planner.propose_metrics(
            research_objective="研究未知",
            user_metrics=["some_unknown_metric"],
            experiment_type="laminar_pipe",
        )

        assert isinstance(plan.unknown_metrics, list)
        for m in plan.unknown_metrics:
            assert isinstance(m, str)

    def test_measurement_plan_still_present(self, planner: MetricPlanner):
        """measurement_plan is still a MeasurementPlan instance."""
        plan = planner.propose_metrics(
            research_objective="研究",
            user_metrics=["pressure_drop"],
            experiment_type="laminar_pipe",
        )

        assert isinstance(plan.measurement_plan, MeasurementPlan)
