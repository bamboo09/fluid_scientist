"""Tests for the analysis engine module."""

import pytest

from fluid_scientist.metric_spec.analysis import (
    MetricReport,
    SimulationData,
    analyze_simulation,
)
from fluid_scientist.metric_spec.models import (
    MetricCategory,
    MetricDefinition,
    MetricQualityCheck,
    MetricQualityStatus,
    MetricSpec,
    MetricTarget,
    QualityCheckType,
)
from fluid_scientist.metric_spec.registry import get_metric_spec


class TestSimulationData:
    def test_max_residual(self):
        data = SimulationData(
            residuals={"Ux": 1e-5, "p": 1e-6, "k": 1e-4},
        )
        assert data.max_residual == 1e-4

    def test_max_residual_empty(self):
        data = SimulationData()
        assert data.max_residual == 0.0

    def test_mass_imbalance_balanced(self):
        data = SimulationData(
            fluxes={"inlet": 1.0, "outlet": -0.99},
        )
        # total = 0.01, avg = 0.005, imbalance = 0.01/0.005 * 100 = 200
        # Actually: |total|/avg*100 = |0.01|/(0.995)*100 = 1.005%
        # avg = |sum|/n = |0.01|/2 = 0.005
        # imbalance = |0.01|/0.005 * 100 = 200%
        # Wait, let me recalculate:
        # total = 1.0 + (-0.99) = 0.01
        # avg = |0.01| / 2 = 0.005
        # imbalance = |0.01| / 0.005 * 100 = 200
        assert data.mass_imbalance_pct == pytest.approx(0.5025, rel=1e-2)

    def test_mass_imbalance_empty(self):
        data = SimulationData()
        assert data.mass_imbalance_pct == 0.0


class TestAnalyzeSimulation:
    def test_analyze_cylinder_passed(self):
        """Analysis with passing residuals and forces."""
        metric_spec = get_metric_spec("cylinder_flow")
        data = SimulationData(
            residuals={"Ux": 1e-6, "p": 1e-7, "k": 1e-6},
            forces={"drag_coefficient": 1.2, "lift_coefficient": 0.1},
            fluxes={"inlet": 1.0, "outlet": -0.999},
            max_courant=0.5,
        )
        report = analyze_simulation(data, metric_spec)
        assert isinstance(report, MetricReport)
        assert report.overall_status == MetricQualityStatus.PASSED
        assert len(report.metric_results) > 0
        assert len(report.quality_check_outcomes) > 0

    def test_analyze_failed_residuals(self):
        """Analysis with failing residuals."""
        metric_spec = get_metric_spec("cylinder_flow")
        data = SimulationData(
            residuals={"Ux": 1e-2, "p": 1e-2},
            forces={"drag_coefficient": 1.2},
            fluxes={"inlet": 1.0, "outlet": -1.0},
        )
        report = analyze_simulation(data, metric_spec)
        assert report.overall_status == MetricQualityStatus.FAILED
        assert any("exceeds" in o.message for o in report.quality_check_outcomes)

    def test_analyze_failed_mass_imbalance(self):
        """Analysis with failing mass imbalance."""
        metric_spec = get_metric_spec("laminar_pipe")
        data = SimulationData(
            residuals={"Ux": 1e-6, "p": 1e-6},
            fluxes={"inlet": 1.0, "outlet": -0.5},
        )
        report = analyze_simulation(data, metric_spec)
        assert report.overall_status in (
            MetricQualityStatus.FAILED,
            MetricQualityStatus.WARNING,
        )

    def test_analyze_with_courant_check(self):
        """Courant number check when max_courant is provided."""
        metric_spec = MetricSpec(
            spec_id="test-courant",
            experiment_type="test",
            metrics=(
                MetricDefinition(
                    metric_id="residual_tolerance",
                    display_name="Residual",
                    category=MetricCategory.CONVERGENCE,
                ),
            ),
            quality_checks=(
                MetricQualityCheck(
                    check_id="courant",
                    check_type=QualityCheckType.COURANT_NUMBER,
                    threshold=1.0,
                ),
            ),
        )
        data = SimulationData(
            residuals={"Ux": 1e-6},
            max_courant=2.5,
        )
        report = analyze_simulation(data, metric_spec)
        assert report.overall_status == MetricQualityStatus.FAILED

    def test_analyze_with_gci(self):
        """GCI check when gci_value is provided."""
        metric_spec = MetricSpec(
            spec_id="test-gci",
            experiment_type="test",
            metrics=(
                MetricDefinition(
                    metric_id="drag",
                    display_name="Drag",
                    category=MetricCategory.PHYSICAL,
                ),
            ),
            quality_checks=(
                MetricQualityCheck(
                    check_id="gci",
                    check_type=QualityCheckType.GCI,
                    threshold=0.05,
                ),
            ),
        )
        data = SimulationData(
            forces={"drag": 1.0},
            gci_value=0.01,
        )
        report = analyze_simulation(data, metric_spec)
        assert report.overall_status == MetricQualityStatus.PASSED

    def test_analyze_metric_with_target(self):
        """Metric with target should get a range check."""
        metric_spec = MetricSpec(
            spec_id="test-target",
            experiment_type="test",
            metrics=(
                MetricDefinition(
                    metric_id="cd",
                    display_name="CD",
                    category=MetricCategory.PHYSICAL,
                    target=MetricTarget(target_value=1.0, tolerance_pct=5),
                    critical=True,
                ),
            ),
        )
        data = SimulationData(forces={"cd": 1.02})
        report = analyze_simulation(data, metric_spec)
        cd_result = next(
            r for r in report.metric_results if r.metric_id == "cd"
        )
        assert cd_result.status == MetricQualityStatus.PASSED

    def test_analyze_metric_target_failed(self):
        """Metric with target outside tolerance should fail."""
        metric_spec = MetricSpec(
            spec_id="test-target-fail",
            experiment_type="test",
            metrics=(
                MetricDefinition(
                    metric_id="cd",
                    display_name="CD",
                    category=MetricCategory.PHYSICAL,
                    target=MetricTarget(target_value=1.0, tolerance_pct=5),
                    critical=True,
                ),
            ),
        )
        data = SimulationData(forces={"cd": 2.0})
        report = analyze_simulation(data, metric_spec)
        cd_result = next(
            r for r in report.metric_results if r.metric_id == "cd"
        )
        assert cd_result.status == MetricQualityStatus.FAILED

    def test_report_summary_not_empty(self):
        metric_spec = get_metric_spec("cylinder_flow")
        data = SimulationData(
            residuals={"Ux": 1e-6},
            forces={"drag_coefficient": 1.2},
        )
        report = analyze_simulation(data, metric_spec)
        assert len(report.summary) > 0
        assert "cylinder_flow" in report.summary

    def test_report_to_dict(self):
        metric_spec = get_metric_spec("cylinder_flow")
        data = SimulationData(
            residuals={"Ux": 1e-6},
            forces={"drag_coefficient": 1.2},
        )
        report = analyze_simulation(data, metric_spec)
        d = report.to_dict()
        assert "spec_id" in d
        assert "metric_results" in d
        assert "quality_checks" in d
        assert "overall_status" in d
        assert d["overall_status"] == "passed"

    def test_analyze_missing_metric_value(self):
        """Metrics without corresponding data should have None value."""
        metric_spec = MetricSpec(
            spec_id="test-missing",
            experiment_type="test",
            metrics=(
                MetricDefinition(
                    metric_id="nonexistent",
                    display_name="Nonexistent",
                    category=MetricCategory.PHYSICAL,
                ),
            ),
        )
        data = SimulationData()
        report = analyze_simulation(data, metric_spec)
        result = report.metric_results[0]
        assert result.value is None
        assert result.status == MetricQualityStatus.NOT_CHECKED
