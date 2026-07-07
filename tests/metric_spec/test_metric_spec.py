"""Tests for the MetricSpec system."""

import pytest

from fluid_scientist.metric_spec.models import (
    MetricCategory,
    MetricDefinition,
    MetricQualityCheck,
    MetricQualityStatus,
    MetricResult,
    MetricSpec,
    MetricTarget,
    QualityCheckType,
)
from fluid_scientist.metric_spec.quality import (
    QualityCheckOutcome,
    aggregate_status,
    calculate_gci,
    check_courant_number,
    check_gci,
    check_mass_imbalance,
    check_range,
    check_residual_tolerance,
    evaluate_result,
)
from fluid_scientist.metric_spec.registry import (
    get_metric_spec,
    registered_types,
)

# --- Model validation tests ---


class TestMetricTarget:
    def test_valid_point_target(self):
        t = MetricTarget(target_value=1.5, tolerance_pct=10)
        assert t.target_value == 1.5

    def test_valid_range_target(self):
        t = MetricTarget(range_min=0.1, range_max=0.5)
        assert t.range_min == 0.1
        assert t.range_max == 0.5

    def test_no_target_raises(self):
        with pytest.raises(ValueError, match="must provide"):
            MetricTarget()

    def test_both_point_and_range_raises(self):
        with pytest.raises(ValueError, match="cannot provide both"):
            MetricTarget(target_value=1.0, range_min=0.0, range_max=2.0)

    def test_inverted_range_raises(self):
        with pytest.raises(ValueError, match="less than"):
            MetricTarget(range_min=1.0, range_max=0.5)


class TestMetricDefinition:
    def test_valid_definition(self):
        d = MetricDefinition(
            metric_id="cd",
            display_name="Drag Coefficient",
            category=MetricCategory.PHYSICAL,
        )
        assert d.metric_id == "cd"
        assert d.unit == "dimensionless"
        assert d.critical is False

    def test_critical_flag(self):
        d = MetricDefinition(
            metric_id="cd",
            display_name="CD",
            category=MetricCategory.PHYSICAL,
            critical=True,
        )
        assert d.critical is True


class TestMetricSpec:
    def _make_spec(self) -> MetricSpec:
        return MetricSpec(
            spec_id="test-001",
            experiment_type="cylinder_flow",
            metrics=(
                MetricDefinition(
                    metric_id="drag_coefficient",
                    display_name="CD",
                    category=MetricCategory.PHYSICAL,
                    critical=True,
                ),
                MetricDefinition(
                    metric_id="residual",
                    display_name="Residual",
                    category=MetricCategory.CONVERGENCE,
                ),
            ),
            quality_checks=(
                MetricQualityCheck(
                    check_id="rc",
                    check_type=QualityCheckType.RESIDUAL_TOLERANCE,
                    threshold=1e-4,
                    metric_id="residual",
                ),
            ),
        )

    def test_valid_spec(self):
        spec = self._make_spec()
        assert len(spec.metrics) == 2
        assert len(spec.quality_checks) == 1

    def test_duplicate_metric_id_rejected(self):
        with pytest.raises(ValueError, match="duplicate"):
            MetricSpec(
                spec_id="test",
                experiment_type="test",
                metrics=(
                    MetricDefinition(
                        metric_id="x",
                        display_name="X",
                        category=MetricCategory.PHYSICAL,
                    ),
                    MetricDefinition(
                        metric_id="x",
                        display_name="X2",
                        category=MetricCategory.PHYSICAL,
                    ),
                ),
            )

    def test_quality_check_unknown_metric_rejected(self):
        with pytest.raises(ValueError, match="unknown metric_id"):
            MetricSpec(
                spec_id="test",
                experiment_type="test",
                metrics=(
                    MetricDefinition(
                        metric_id="x",
                        display_name="X",
                        category=MetricCategory.PHYSICAL,
                    ),
                ),
                quality_checks=(
                    MetricQualityCheck(
                        check_id="c",
                        check_type=QualityCheckType.RANGE_CHECK,
                        threshold=1.0,
                        metric_id="nonexistent",
                    ),
                ),
            )

    def test_get_metric(self):
        spec = self._make_spec()
        m = spec.get_metric("drag_coefficient")
        assert m is not None
        assert m.critical is True
        assert spec.get_metric("nonexistent") is None

    def test_critical_metrics(self):
        spec = self._make_spec()
        crit = spec.critical_metrics()
        assert len(crit) == 1
        assert crit[0].metric_id == "drag_coefficient"


# --- Registry tests ---


class TestRegistry:
    def test_registered_types(self):
        types = registered_types()
        assert "laminar_pipe" in types
        assert "cylinder_flow" in types
        assert "lid_driven_cavity" in types

    def test_cylinder_metrics(self):
        spec = get_metric_spec("cylinder_flow")
        assert spec.experiment_type == "cylinder_flow"
        ids = {m.metric_id for m in spec.metrics}
        assert "drag_coefficient" in ids
        assert "lift_coefficient" in ids
        assert "strouhal_number" in ids

    def test_pipe_metrics(self):
        spec = get_metric_spec("laminar_pipe")
        ids = {m.metric_id for m in spec.metrics}
        assert "pressure_drop" in ids
        assert "friction_factor" in ids
        assert "reynolds_number" in ids

    def test_cavity_metrics(self):
        spec = get_metric_spec("lid_driven_cavity")
        ids = {m.metric_id for m in spec.metrics}
        assert "velocity_profile" in ids
        assert "vortex_center_x" in ids
        assert "vortex_center_y" in ids

    def test_standard_quality_checks_present(self):
        spec = get_metric_spec("cylinder_flow")
        check_ids = {c.check_id for c in spec.quality_checks}
        assert "residual_convergence" in check_ids
        assert "mass_conservation" in check_ids

    def test_unknown_type_raises(self):
        with pytest.raises(KeyError, match="no standard metrics"):
            get_metric_spec("nonexistent_type")

    def test_critical_metric_marked(self):
        spec = get_metric_spec("cylinder_flow")
        drag = spec.get_metric("drag_coefficient")
        assert drag is not None
        assert drag.critical is True


# --- Quality check tests ---


class TestResidualTolerance:
    def test_passed(self):
        outcome = check_residual_tolerance(1e-6, 1e-4)
        assert outcome.status == MetricQualityStatus.PASSED

    def test_warning(self):
        outcome = check_residual_tolerance(5e-4, 1e-4)
        assert outcome.status == MetricQualityStatus.WARNING

    def test_failed(self):
        outcome = check_residual_tolerance(1e-2, 1e-4)
        assert outcome.status == MetricQualityStatus.FAILED

    def test_negative_residual_fails(self):
        outcome = check_residual_tolerance(-1e-6, 1e-4)
        assert outcome.status == MetricQualityStatus.FAILED


class TestMassImbalance:
    def test_passed(self):
        outcome = check_mass_imbalance(0.1, 1.0)
        assert outcome.status == MetricQualityStatus.PASSED

    def test_warning(self):
        outcome = check_mass_imbalance(1.5, 1.0)
        assert outcome.status == MetricQualityStatus.WARNING

    def test_failed(self):
        outcome = check_mass_imbalance(5.0, 1.0)
        assert outcome.status == MetricQualityStatus.FAILED

    def test_negative_imbalance_uses_absolute(self):
        outcome = check_mass_imbalance(-0.5, 1.0)
        assert outcome.status == MetricQualityStatus.PASSED


class TestCourantNumber:
    def test_passed(self):
        outcome = check_courant_number(0.5, 1.0)
        assert outcome.status == MetricQualityStatus.PASSED

    def test_warning(self):
        outcome = check_courant_number(1.5, 1.0)
        assert outcome.status == MetricQualityStatus.WARNING

    def test_failed(self):
        outcome = check_courant_number(3.0, 1.0)
        assert outcome.status == MetricQualityStatus.FAILED


class TestGCI:
    def test_calculate_gci_basic(self):
        # Fine=1.0, Coarse=1.1, r=2, p=2, Fs=1.25
        # epsilon = 0.1, denominator = 3
        # GCI = 1.25 * 0.1 / 3 = 0.04167
        gci = calculate_gci(1.0, 1.1, grid_ratio=2.0, order=2.0, safety_factor=1.25)
        assert pytest.approx(gci, rel=1e-3) == 0.04167

    def test_calculate_gci_zero_fine_raises(self):
        with pytest.raises(ValueError, match="zero"):
            calculate_gci(0.0, 1.0)

    def test_calculate_gci_bad_ratio_raises(self):
        with pytest.raises(ValueError, match="grid_ratio"):
            calculate_gci(1.0, 1.1, grid_ratio=1.0)

    def test_check_gci_passed(self):
        outcome = check_gci(0.01, 0.05)
        assert outcome.status == MetricQualityStatus.PASSED

    def test_check_gci_warning(self):
        outcome = check_gci(0.08, 0.05)
        assert outcome.status == MetricQualityStatus.WARNING

    def test_check_gci_failed(self):
        outcome = check_gci(0.2, 0.05)
        assert outcome.status == MetricQualityStatus.FAILED


class TestRangeCheck:
    def test_point_target_passed(self):
        target = MetricTarget(target_value=1.0, tolerance_pct=5)
        outcome = check_range(1.02, target)
        assert outcome.status == MetricQualityStatus.PASSED

    def test_point_target_failed(self):
        target = MetricTarget(target_value=1.0, tolerance_pct=5)
        outcome = check_range(1.5, target)
        assert outcome.status == MetricQualityStatus.FAILED

    def test_range_target_passed(self):
        target = MetricTarget(range_min=0.0, range_max=2.0)
        outcome = check_range(1.0, target)
        assert outcome.status == MetricQualityStatus.PASSED

    def test_range_target_below_min(self):
        target = MetricTarget(range_min=0.5, range_max=2.0)
        outcome = check_range(0.1, target)
        assert outcome.status == MetricQualityStatus.FAILED

    def test_range_target_above_max(self):
        target = MetricTarget(range_min=0.0, range_max=2.0)
        outcome = check_range(3.0, target)
        assert outcome.status == MetricQualityStatus.FAILED


class TestAggregateStatus:
    def test_all_passed(self):
        outcomes = [
            QualityCheckOutcome(
                QualityCheckType.RESIDUAL_TOLERANCE,
                MetricQualityStatus.PASSED, 1e-6, 1e-4, "ok",
            ),
            QualityCheckOutcome(
                QualityCheckType.MASS_IMBALANCE,
                MetricQualityStatus.PASSED, 0.1, 1.0, "ok",
            ),
        ]
        assert aggregate_status(outcomes) == MetricQualityStatus.PASSED

    def test_one_failed(self):
        outcomes = [
            QualityCheckOutcome(
                QualityCheckType.RESIDUAL_TOLERANCE,
                MetricQualityStatus.PASSED, 1e-6, 1e-4, "ok",
            ),
            QualityCheckOutcome(
                QualityCheckType.MASS_IMBALANCE,
                MetricQualityStatus.FAILED, 5.0, 1.0, "bad",
            ),
        ]
        assert aggregate_status(outcomes) == MetricQualityStatus.FAILED

    def test_warning_dominates_passed(self):
        outcomes = [
            QualityCheckOutcome(
                QualityCheckType.RESIDUAL_TOLERANCE,
                MetricQualityStatus.PASSED, 1e-6, 1e-4, "ok",
            ),
            QualityCheckOutcome(
                QualityCheckType.MASS_IMBALANCE,
                MetricQualityStatus.WARNING, 1.5, 1.0, "warn",
            ),
        ]
        assert aggregate_status(outcomes) == MetricQualityStatus.WARNING

    def test_empty_returns_not_checked(self):
        assert aggregate_status([]) == MetricQualityStatus.NOT_CHECKED


class TestEvaluateResult:
    def test_evaluate_with_passed_checks(self):
        result = MetricResult(metric_id="cd", value=1.0)
        outcomes = [
            QualityCheckOutcome(
                QualityCheckType.RANGE_CHECK,
                MetricQualityStatus.PASSED, 1.0, 1.0, "ok",
            ),
        ]
        evaluated = evaluate_result(result, outcomes)
        assert evaluated.status == MetricQualityStatus.PASSED
        assert len(evaluated.quality_checks) == 1

    def test_evaluate_with_failed_checks(self):
        result = MetricResult(metric_id="cd", value=5.0)
        outcomes = [
            QualityCheckOutcome(
                QualityCheckType.RANGE_CHECK,
                MetricQualityStatus.FAILED, 5.0, 1.0, "bad",
            ),
        ]
        evaluated = evaluate_result(result, outcomes)
        assert evaluated.status == MetricQualityStatus.FAILED

    def test_evaluate_no_checks(self):
        result = MetricResult(metric_id="cd", value=1.0)
        evaluated = evaluate_result(result, [])
        assert evaluated.status == MetricQualityStatus.NOT_CHECKED
        assert len(evaluated.quality_checks) == 0
