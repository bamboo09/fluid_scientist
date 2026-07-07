"""Tests for MetricExecutor and ScientificAnalyzer (Commit 10).

These tests verify that:
1. MetricExecutor calculates all supported metrics deterministically
2. Quality checks are performed for spectral metrics (Strouhal)
3. Missing data and unknown metrics are handled gracefully
4. ScientificAnalyzer produces all 6 analysis layers
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from fluid_scientist.results.analysis import (
    AnalysisLayer,
    ScientificAnalysis,
    ScientificAnalyzer,
)
from fluid_scientist.results.metric_executor import (
    MetricExecutor,
    QualityCheckResult,
)
from fluid_scientist.results.models import MetricResult, SimulationData


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _make_pressure_data(
    inlet_values: list[float] | None = None,
    outlet_values: list[float] | None = None,
) -> SimulationData:
    """Create SimulationData with inlet/outlet pressure surface field values."""
    inlet_values = inlet_values if inlet_values is not None else [100.0, 100.0, 100.0, 100.0, 100.0, 100.0]
    outlet_values = outlet_values if outlet_values is not None else [20.0, 20.0, 20.0, 20.0, 20.0, 20.0]
    return SimulationData(
        surface_field_values={
            "pressure_inlet_average": inlet_values,
            "pressure_outlet_average": outlet_values,
        },
    )


def _make_force_coeffs_data(
    cd_values: list[float] | None = None,
    cl_values: list[float] | None = None,
) -> SimulationData:
    """Create SimulationData with force coefficients."""
    cd_values = cd_values if cd_values is not None else [1.2, 1.3, 1.25]
    cl_values = cl_values if cl_values is not None else [0.4, 0.5, 0.45]
    return SimulationData(
        force_coefficients={"Cd": cd_values, "Cl": cl_values, "Cm": [0.1, 0.12, 0.11]},
    )


def _make_strouhal_data(
    frequency: float = 10.0,
    n_samples: int = 200,
    dt: float = 0.01,
    amplitude: float = 0.5,
) -> SimulationData:
    """Create SimulationData with a synthetic Cl time series for FFT.

    The Cl signal is a pure sinusoid at the given frequency plus small noise.
    """
    t = np.arange(n_samples) * dt
    cl = amplitude * np.sin(2 * math.pi * frequency * t)
    # Add tiny noise so the FFT peak is still clearly dominant
    rng = np.random.default_rng(42)
    cl = cl + 0.001 * rng.standard_normal(n_samples)
    return SimulationData(
        force_coefficients={"Cl": cl.tolist(), "Cd": [0.0] * n_samples},
    )


def _make_residual_data(residuals: dict[str, float] | None = None) -> SimulationData:
    """Create SimulationData with final residuals."""
    residuals = residuals if residuals is not None else {"Ux": 1e-6, "p": 1e-7}
    return SimulationData(final_residuals=residuals)


def _make_courant_data(max_courant: float = 0.5) -> SimulationData:
    """Create SimulationData with Courant number."""
    return SimulationData(courant_numbers=[0.1, 0.3, max_courant], max_courant=max_courant)


# --------------------------------------------------------------------------- #
# MetricExecutor — pressure_drop
# --------------------------------------------------------------------------- #


class TestPressureDrop:
    """Test 1: pressure_drop calculation from surface field values."""

    def test_pressure_drop_correct_value(self):
        """pressure_drop = p_inlet - p_outlet."""
        data = _make_pressure_data(inlet_values=[100.0] * 6, outlet_values=[20.0] * 6)
        executor = MetricExecutor()
        result = executor.execute("pressure_drop", data)

        assert result.metric_id == "pressure_drop"
        assert result.value == pytest.approx(80.0)
        assert result.unit == "Pa"
        assert result.confidence in ("high", "medium")
        assert not result.data_missing

    def test_pressure_drop_stable_values_high_confidence(self):
        """Stable pressure values yield high confidence."""
        data = _make_pressure_data(inlet_values=[100.0] * 10, outlet_values=[20.0] * 10)
        executor = MetricExecutor()
        result = executor.execute("pressure_drop", data)

        assert result.value == pytest.approx(80.0)

    def test_pressure_drop_missing_outlet(self):
        """Missing outlet pressure returns data_missing."""
        data = SimulationData(
            surface_field_values={"pressure_inlet_average": [100.0, 100.0]},
        )
        executor = MetricExecutor()
        result = executor.execute("pressure_drop", data)

        assert result.data_missing is True
        assert result.confidence == "failed"
        assert result.value is None


# --------------------------------------------------------------------------- #
# MetricExecutor — drag_coefficient
# --------------------------------------------------------------------------- #


class TestDragCoefficient:
    """Test 2: drag_coefficient extraction from forceCoeffs."""

    def test_drag_coefficient_time_averaged(self):
        """Cd is the time-averaged value from forceCoeffs."""
        data = _make_force_coeffs_data(cd_values=[1.2, 1.3, 1.25])
        executor = MetricExecutor()
        result = executor.execute("drag_coefficient", data)

        assert result.metric_id == "drag_coefficient"
        # mean of [1.2, 1.3, 1.25] = 1.25
        assert result.value == pytest.approx(1.25)
        assert result.unit == "dimensionless"
        assert not result.data_missing

    def test_drag_coefficient_missing_cd(self):
        """Missing Cd data returns data_missing."""
        data = SimulationData(force_coefficients={"Cl": [0.5]})
        executor = MetricExecutor()
        result = executor.execute("drag_coefficient", data)

        assert result.data_missing is True
        assert result.confidence == "failed"


# --------------------------------------------------------------------------- #
# MetricExecutor — lift_coefficient
# --------------------------------------------------------------------------- #


class TestLiftCoefficient:
    """Test 3: lift_coefficient extraction from forceCoeffs."""

    def test_lift_coefficient_time_averaged(self):
        """Cl is the time-averaged value from forceCoeffs."""
        data = _make_force_coeffs_data(cl_values=[0.4, 0.5, 0.45])
        executor = MetricExecutor()
        result = executor.execute("lift_coefficient", data)

        assert result.metric_id == "lift_coefficient"
        # mean of [0.4, 0.5, 0.45] = 0.45
        assert result.value == pytest.approx(0.45)
        assert result.unit == "dimensionless"
        assert result.confidence == "high"
        assert not result.data_missing

    def test_lift_coefficient_missing_cl(self):
        """Missing Cl data returns data_missing."""
        data = SimulationData(force_coefficients={"Cd": [1.2]})
        executor = MetricExecutor()
        result = executor.execute("lift_coefficient", data)

        assert result.data_missing is True
        assert result.confidence == "failed"


# --------------------------------------------------------------------------- #
# MetricExecutor — strouhal_number
# --------------------------------------------------------------------------- #


class TestStrouhalNumber:
    """Tests 4 & 5: Strouhal number via FFT with quality checks."""

    def test_strouhal_number_fft(self):
        """Test 4: St = f * D / U from FFT of Cl."""
        frequency = 10.0  # Hz
        diameter = 0.1
        velocity = 1.0
        dt = 0.01

        data = _make_strouhal_data(frequency=frequency, n_samples=200, dt=dt)
        executor = MetricExecutor()
        result = executor.execute(
            "strouhal_number",
            data,
            parameters={"diameter": diameter, "mean_velocity": velocity, "time_step": dt},
        )

        expected_st = frequency * diameter / velocity
        assert result.metric_id == "strouhal_number"
        assert result.value is not None
        assert result.value == pytest.approx(expected_st, rel=0.05)
        assert result.unit == "dimensionless"
        assert not result.data_missing

    def test_strouhal_quality_checks_present(self):
        """Test 5: Strouhal quality checks include required checks."""
        data = _make_strouhal_data(frequency=10.0, n_samples=200, dt=0.01)
        executor = MetricExecutor()
        result = executor.execute(
            "strouhal_number",
            data,
            parameters={"diameter": 0.1, "mean_velocity": 1.0, "time_step": 0.01},
        )

        check_names = {q["name"] for q in result.quality_checks}
        assert "data_length" in check_names
        assert "signal_stationarity" in check_names
        assert "peak_prominence" in check_names
        assert "frequency_resolution" in check_names
        assert "statistical_cycles" in check_names

    def test_strouhal_insufficient_data(self):
        """Fewer than 20 Cl samples returns data_missing."""
        data = SimulationData(force_coefficients={"Cl": [0.1] * 10})
        executor = MetricExecutor()
        result = executor.execute(
            "strouhal_number",
            data,
            parameters={"diameter": 0.1, "mean_velocity": 1.0},
        )

        assert result.data_missing is True
        assert result.confidence == "failed"

    def test_strouhal_zero_velocity(self):
        """Zero velocity returns data_missing."""
        data = _make_strouhal_data(frequency=10.0, n_samples=200, dt=0.01)
        executor = MetricExecutor()
        result = executor.execute(
            "strouhal_number",
            data,
            parameters={"diameter": 0.1, "mean_velocity": 0.0, "time_step": 0.01},
        )

        assert result.data_missing is True
        assert result.confidence == "failed"


# --------------------------------------------------------------------------- #
# MetricExecutor — velocity_uniformity
# --------------------------------------------------------------------------- #


class TestVelocityUniformity:
    """Test 6: velocity_uniformity CV = sigma_u / mean_u."""

    def test_velocity_uniformity_proper_cv(self):
        """CV calculated as std/mean from velocity time series."""
        vel = [1.0, 1.1, 0.9, 1.05, 0.95, 1.0, 1.02, 0.98, 1.01, 0.99]
        data = SimulationData(
            surface_field_values={
                "velocity_mean_outlet": vel,
            },
        )
        executor = MetricExecutor()
        result = executor.execute("velocity_uniformity", data)

        assert result.metric_id == "velocity_uniformity"
        assert result.value is not None
        # CV = std(u) / |mean(u)| computed over steady-state portion
        from fluid_scientist.results.metric_executor import _time_averaged_stats
        mean_u, std_u, _ = _time_averaged_stats(vel)
        expected_cv = std_u / abs(mean_u)
        assert result.value == pytest.approx(expected_cv, rel=0.01)
        assert result.unit == "dimensionless"
        assert result.confidence == "high"

    def test_velocity_uniformity_missing_data(self):
        """Missing velocity mean returns data_missing."""
        data = SimulationData(surface_field_values={"pressure_inlet": [100.0]})
        executor = MetricExecutor()
        result = executor.execute("velocity_uniformity", data)

        assert result.data_missing is True
        assert result.confidence == "failed"

    def test_outlet_velocity_uniformity_alias(self):
        """outlet_velocity_uniformity is handled as an alias."""
        vel = [1.0, 1.1, 0.9, 1.05, 0.95, 1.0, 1.02, 0.98, 1.01, 0.99]
        data = SimulationData(
            surface_field_values={
                "velocity_mean_outlet": vel,
            },
        )
        executor = MetricExecutor()
        result = executor.execute("outlet_velocity_uniformity", data)

        assert result.metric_id == "velocity_uniformity"
        assert result.value is not None


# --------------------------------------------------------------------------- #
# MetricExecutor — reynolds_number
# --------------------------------------------------------------------------- #


class TestReynoldsNumber:
    """Test 7: Reynolds number Re = U * D / nu."""

    def test_reynolds_number_calculation(self):
        """Re = U * D / nu."""
        data = SimulationData()
        executor = MetricExecutor()
        result = executor.execute(
            "reynolds_number",
            data,
            parameters={
                "mean_velocity": 0.1,
                "diameter": 0.05,
                "kinematic_viscosity": 1e-6,
            },
        )

        expected_re = 0.1 * 0.05 / 1e-6  # = 5000
        assert result.metric_id == "reynolds_number"
        assert result.value == pytest.approx(expected_re)
        assert result.unit == "dimensionless"
        assert result.confidence == "high"

    def test_reynolds_number_laminar(self):
        """Low Re is calculated correctly."""
        data = SimulationData()
        executor = MetricExecutor()
        result = executor.execute(
            "reynolds_number",
            data,
            parameters={
                "mean_velocity": 0.01,
                "diameter": 0.05,
                "kinematic_viscosity": 1e-6,
            },
        )

        expected_re = 0.01 * 0.05 / 1e-6  # = 500
        assert result.value == pytest.approx(expected_re)

    def test_reynolds_number_zero_viscosity(self):
        """Zero viscosity returns failure."""
        data = SimulationData()
        executor = MetricExecutor()
        result = executor.execute(
            "reynolds_number",
            data,
            parameters={"mean_velocity": 0.1, "diameter": 0.05, "kinematic_viscosity": 0},
        )

        assert result.confidence == "failed"
        assert result.value is None


# --------------------------------------------------------------------------- #
# MetricExecutor — friction_factor
# --------------------------------------------------------------------------- #


class TestFrictionFactor:
    """Test 8: friction factor from pressure drop."""

    def test_friction_factor_calculation(self):
        """f = dp / (0.5 * rho * U^2 * L / D)."""
        data = _make_pressure_data(
            inlet_values=[110.0] * 6,
            outlet_values=[10.0] * 6,  # dp = 100
        )
        executor = MetricExecutor()
        result = executor.execute(
            "friction_factor",
            data,
            parameters={
                "density": 1000.0,
                "mean_velocity": 0.1,
                "diameter": 0.05,
                "length": 1.0,
            },
        )

        dp = 100.0
        expected_f = dp / (0.5 * 1000.0 * 0.1**2 * 1.0 / 0.05)
        assert result.metric_id == "friction_factor"
        assert result.value == pytest.approx(expected_f)
        assert result.unit == "dimensionless"

    def test_friction_factor_missing_pressure(self):
        """Missing inlet/outlet pressure returns data_missing."""
        data = SimulationData()
        executor = MetricExecutor()
        result = executor.execute("friction_factor", data)

        assert result.data_missing is True


# --------------------------------------------------------------------------- #
# MetricExecutor — residual_tolerance
# --------------------------------------------------------------------------- #


class TestResidualTolerance:
    """Test 9: residual_tolerance returns max final residual."""

    def test_residual_tolerance_max(self):
        """Returns max of final residuals."""
        data = _make_residual_data(residuals={"Ux": 1e-6, "p": 1e-7, "Uy": 5e-6})
        executor = MetricExecutor()
        result = executor.execute("residual_tolerance", data)

        assert result.metric_id == "residual_tolerance"
        assert result.value == pytest.approx(5e-6)
        assert result.unit == "dimensionless"
        assert not result.data_missing

    def test_residual_tolerance_below_threshold(self):
        """Residual below 1e-4 yields high confidence."""
        data = _make_residual_data(residuals={"Ux": 1e-6})
        executor = MetricExecutor()
        result = executor.execute("residual_tolerance", data)

        assert result.confidence == "high"
        assert result.quality_checks[0]["passed"] is True

    def test_residual_tolerance_above_threshold(self):
        """Residual above 1e-4 yields medium confidence."""
        data = _make_residual_data(residuals={"Ux": 1e-3})
        executor = MetricExecutor()
        result = executor.execute("residual_tolerance", data)

        assert result.confidence == "medium"
        assert result.quality_checks[0]["passed"] is False

    def test_residual_tolerance_empty(self):
        """Empty residuals returns data_missing."""
        data = SimulationData()
        executor = MetricExecutor()
        result = executor.execute("residual_tolerance", data)

        assert result.data_missing is True


# --------------------------------------------------------------------------- #
# MetricExecutor — max_courant
# --------------------------------------------------------------------------- #


class TestMaxCourant:
    """Test 10: max_courant returns max Courant number."""

    def test_max_courant_below_threshold(self):
        """Courant < 1.0 yields high confidence."""
        data = _make_courant_data(max_courant=0.5)
        executor = MetricExecutor()
        result = executor.execute("max_courant", data)

        assert result.metric_id == "max_courant"
        assert result.value == pytest.approx(0.5)
        assert result.unit == "dimensionless"
        assert result.confidence == "high"
        assert result.quality_checks[0]["passed"] is True

    def test_max_courant_above_threshold(self):
        """Courant > 1.0 yields medium confidence."""
        data = _make_courant_data(max_courant=1.5)
        executor = MetricExecutor()
        result = executor.execute("max_courant", data)

        assert result.confidence == "medium"
        assert result.quality_checks[0]["passed"] is False

    def test_max_courant_missing(self):
        """None max_courant returns data_missing."""
        data = SimulationData()
        executor = MetricExecutor()
        result = executor.execute("max_courant", data)

        assert result.data_missing is True


# --------------------------------------------------------------------------- #
# MetricExecutor — missing data and unknown metrics
# --------------------------------------------------------------------------- #


class TestMissingAndUnknown:
    """Tests 11 & 12: missing data and unknown metric handling."""

    def test_missing_data_returns_data_missing(self):
        """Test 11: metric in missing_data list returns data_missing=True."""
        data = SimulationData(missing_data=["pressure_drop"])
        executor = MetricExecutor()
        result = executor.execute("pressure_drop", data)

        assert result.data_missing is True
        assert result.confidence == "failed"
        assert result.missing_reason is not None
        assert len(result.warnings) > 0

    def test_unknown_metric_returns_data_missing(self):
        """Test 12: unknown metric returns data_missing=True."""
        data = SimulationData()
        executor = MetricExecutor()
        result = executor.execute("nonexistent_metric", data)

        assert result.data_missing is True
        assert result.confidence == "failed"
        assert result.missing_reason is not None


# --------------------------------------------------------------------------- #
# MetricExecutor — execute_all
# --------------------------------------------------------------------------- #


class TestExecuteAll:
    """Test 13: execute_all runs multiple metrics."""

    def test_execute_all_multiple_metrics(self):
        """execute_all returns a list of MetricResults."""
        data = SimulationData(
            force_coefficients={"Cd": [1.2, 1.3], "Cl": [0.4, 0.5]},
            final_residuals={"Ux": 1e-6},
            max_courant=0.5,
        )
        executor = MetricExecutor()
        results = executor.execute_all(
            ["drag_coefficient", "lift_coefficient", "residual_tolerance", "max_courant"],
            data,
        )

        assert len(results) == 4
        metric_ids = {r.metric_id for r in results}
        assert metric_ids == {"drag_coefficient", "lift_coefficient", "residual_tolerance", "max_courant"}

    def test_execute_all_with_definitions(self):
        """execute_all passes metric definitions."""
        data = _make_pressure_data()
        executor = MetricExecutor()
        results = executor.execute_all(
            ["pressure_drop"],
            data,
            metric_definitions={
                "pressure_drop": {"formula": "p_inlet - p_outlet", "unit": "Pa"},
            },
        )

        assert len(results) == 1
        assert results[0].value == pytest.approx(80.0)


# --------------------------------------------------------------------------- #
# MetricExecutor — mass_flow_rate
# --------------------------------------------------------------------------- #


class TestMassFlowRate:
    """Additional: mass_flow_rate calculation."""

    def test_mass_flow_rate(self):
        """m_dot = rho * U * A."""
        data = SimulationData()
        executor = MetricExecutor()
        result = executor.execute(
            "mass_flow_rate",
            data,
            parameters={"density": 1000.0, "mean_velocity": 0.1, "diameter": 0.05},
        )

        area = math.pi * (0.025) ** 2
        expected = 1000.0 * 0.1 * area
        assert result.value == pytest.approx(expected)
        assert result.unit == "kg/s"


# --------------------------------------------------------------------------- #
# QualityCheckResult
# --------------------------------------------------------------------------- #


class TestQualityCheckResult:
    """QualityCheckResult helper class."""

    def test_to_dict(self):
        qcr = QualityCheckResult("test_check", True, "all good")
        d = qcr.to_dict()
        assert d["name"] == "test_check"
        assert d["passed"] is True
        assert d["message"] == "all good"

    def test_failed_check(self):
        qcr = QualityCheckResult("test_check", False, "failed")
        assert qcr.passed is False


# --------------------------------------------------------------------------- #
# ScientificAnalyzer — all 6 layers
# --------------------------------------------------------------------------- #


class TestScientificAnalyzerLayers:
    """Tests 14-20: ScientificAnalyzer produces all 6 layers."""

    @pytest.fixture
    def sample_results(self) -> list[MetricResult]:
        """Create sample metric results for analysis."""
        return [
            MetricResult(
                metric_id="pressure_drop",
                value=80.0,
                unit="Pa",
                confidence="high",
                quality_checks=[{"name": "stability", "passed": True, "message": "stable"}],
            ),
            MetricResult(
                metric_id="drag_coefficient",
                value=1.25,
                unit="dimensionless",
                confidence="high",
                quality_checks=[],
            ),
            MetricResult(
                metric_id="reynolds_number",
                value=5000.0,
                unit="dimensionless",
                confidence="high",
                quality_checks=[],
            ),
            MetricResult(
                metric_id="missing_metric",
                value=None,
                confidence="failed",
                data_missing=True,
                missing_reason="data not found",
            ),
        ]

    @pytest.fixture
    def sample_simulation_data(self) -> SimulationData:
        """Create sample SimulationData."""
        return SimulationData(
            max_courant=0.5,
            final_continuity_error=1e-6,
        )

    def test_analyze_produces_all_six_layers(self, sample_results, sample_simulation_data):
        """Test 14: analyze() produces all 6 layers."""
        analyzer = ScientificAnalyzer()
        analysis = analyzer.analyze(sample_results, sample_simulation_data)

        assert isinstance(analysis, ScientificAnalysis)
        # All 6 layers should be present (as lists)
        assert hasattr(analysis, "direct_facts")
        assert hasattr(analysis, "numerical_credibility")
        assert hasattr(analysis, "comparisons")
        assert hasattr(analysis, "physical_interpretation")
        assert hasattr(analysis, "hypotheses")
        assert hasattr(analysis, "recommendations")

    def test_direct_facts_contain_values(self, sample_results, sample_simulation_data):
        """Test 15: Direct facts layer contains calculated metric values."""
        analyzer = ScientificAnalyzer()
        analysis = analyzer.analyze(sample_results, sample_simulation_data)

        assert len(analysis.direct_facts) > 0
        # Should contain pressure_drop, drag_coefficient, reynolds_number
        fact_contents = [f.content for f in analysis.direct_facts]
        assert any("pressure_drop" in c for c in fact_contents)
        assert any("drag_coefficient" in c for c in fact_contents)
        assert any("reynolds_number" in c for c in fact_contents)
        # Should NOT contain missing metric
        assert not any("missing_metric" in c for c in fact_contents)

    def test_numerical_credibility_contains_failed_checks(self, sample_results, sample_simulation_data):
        """Test 16: Numerical credibility layer contains failed quality checks."""
        results_with_failure = [
            MetricResult(
                metric_id="residual_tolerance",
                value=1e-3,
                unit="dimensionless",
                confidence="medium",
                quality_checks=[
                    {"name": "below_tolerance", "passed": False, "message": "Max residual: 1.00e-03"},
                ],
            ),
        ]
        analyzer = ScientificAnalyzer()
        analysis = analyzer.analyze(results_with_failure, sample_simulation_data)

        assert len(analysis.numerical_credibility) > 0
        credibility_content = analysis.numerical_credibility[0].content
        assert "residual_tolerance" in credibility_content
        assert "below_tolerance" in credibility_content

    def test_comparison_layer_shows_relative_error(self, sample_results, sample_simulation_data):
        """Test 17: Comparison layer shows relative error vs benchmark."""
        benchmark_values = {"drag_coefficient": 1.30}
        analyzer = ScientificAnalyzer()
        analysis = analyzer.analyze(
            sample_results, sample_simulation_data, benchmark_values=benchmark_values,
        )

        assert len(analysis.comparisons) > 0
        comp_content = analysis.comparisons[0].content
        assert "drag_coefficient" in comp_content
        assert "relative_error" in comp_content

    def test_physical_interpretation_contains_reynolds_regime(self, sample_results, sample_simulation_data):
        """Test 18: Physical interpretation layer contains Reynolds number regime."""
        analyzer = ScientificAnalyzer()
        analysis = analyzer.analyze(sample_results, sample_simulation_data)

        assert len(analysis.physical_interpretation) > 0
        interp_contents = [p.content for p in analysis.physical_interpretation]
        # Re=5000 is turbulent
        assert any("湍流" in c for c in interp_contents)

    def test_recommendations_suggest_recompile_for_missing(self, sample_results, sample_simulation_data):
        """Test 19: Recommendations layer suggests recompile for missing data."""
        analyzer = ScientificAnalyzer()
        analysis = analyzer.analyze(sample_results, sample_simulation_data)

        assert len(analysis.recommendations) > 0
        rec_contents = [r.content for r in analysis.recommendations]
        assert any("重新编译" in c for c in rec_contents)

    def test_overall_confidence_reflects_metrics(self, sample_results, sample_simulation_data):
        """Test 20: Overall confidence reflects metric confidence levels."""
        analyzer = ScientificAnalyzer()
        analysis = analyzer.analyze(sample_results, sample_simulation_data)

        # With high confidence metrics and one missing, overall should be high
        assert analysis.overall_confidence in ("high", "medium")

    def test_overall_confidence_low_with_low_metric(self, sample_simulation_data):
        """Overall confidence is low when a metric has low confidence."""
        results = [
            MetricResult(
                metric_id="strouhal_number",
                value=0.3,
                unit="dimensionless",
                confidence="low",
            ),
        ]
        analyzer = ScientificAnalyzer()
        analysis = analyzer.analyze(results, sample_simulation_data)

        assert analysis.overall_confidence == "low"

    def test_overall_confidence_medium(self, sample_simulation_data):
        """Overall confidence is medium when metrics are medium."""
        results = [
            MetricResult(
                metric_id="pressure_drop",
                value=80.0,
                unit="Pa",
                confidence="medium",
            ),
        ]
        analyzer = ScientificAnalyzer()
        analysis = analyzer.analyze(results, sample_simulation_data)

        assert analysis.overall_confidence == "medium"

    def test_hypotheses_for_low_confidence(self, sample_simulation_data):
        """Hypotheses layer includes low-confidence metrics."""
        results = [
            MetricResult(
                metric_id="strouhal_number",
                value=0.3,
                unit="dimensionless",
                confidence="low",
            ),
        ]
        analyzer = ScientificAnalyzer()
        analysis = analyzer.analyze(results, sample_simulation_data)

        assert len(analysis.hypotheses) > 0
        assert "strouhal_number" in analysis.hypotheses[0].content

    def test_key_findings_contain_values(self, sample_results, sample_simulation_data):
        """Key findings contain calculated metric values."""
        analyzer = ScientificAnalyzer()
        analysis = analyzer.analyze(sample_results, sample_simulation_data)

        assert len(analysis.key_findings) > 0
        assert any("pressure_drop" in f for f in analysis.key_findings)

    def test_limitations_contain_missing_and_warnings(self, sample_results, sample_simulation_data):
        """Limitations include data missing and warnings."""
        analyzer = ScientificAnalyzer()
        analysis = analyzer.analyze(sample_results, sample_simulation_data)

        assert len(analysis.limitations) > 0
        assert any("missing_metric" in l for l in analysis.limitations)

    def test_recommendations_for_high_courant(self, sample_simulation_data):
        """Recommendations include timestep reduction when Courant > 1."""
        data = SimulationData(max_courant=1.5)
        results = [
            MetricResult(metric_id="max_courant", value=1.5, unit="dimensionless", confidence="medium"),
        ]
        analyzer = ScientificAnalyzer()
        analysis = analyzer.analyze(results, data)

        rec_contents = [r.content for r in analysis.recommendations]
        assert any("时间步长" in c for c in rec_contents)

    def test_empty_results(self, sample_simulation_data):
        """Empty metric results still produce a valid analysis."""
        analyzer = ScientificAnalyzer()
        analysis = analyzer.analyze([], sample_simulation_data)

        assert isinstance(analysis, ScientificAnalysis)
        assert len(analysis.direct_facts) == 0
        assert analysis.overall_confidence == "high"


# --------------------------------------------------------------------------- #
# ScientificAnalyzer — _interpret_metric
# --------------------------------------------------------------------------- #


class TestInterpretMetric:
    """Test physical interpretation for specific metrics."""

    def test_interpret_reynolds_laminar(self):
        """Re < 2300 is laminar."""
        analyzer = ScientificAnalyzer()
        result = MetricResult(metric_id="reynolds_number", value=500.0, unit="dimensionless")
        interp = analyzer._interpret_metric(result)
        assert interp is not None
        assert "层流" in interp

    def test_interpret_reynolds_transitional(self):
        """2300 <= Re < 4000 is transitional."""
        analyzer = ScientificAnalyzer()
        result = MetricResult(metric_id="reynolds_number", value=3000.0, unit="dimensionless")
        interp = analyzer._interpret_metric(result)
        assert interp is not None
        assert "过渡区" in interp

    def test_interpret_reynolds_turbulent(self):
        """Re >= 4000 is turbulent."""
        analyzer = ScientificAnalyzer()
        result = MetricResult(metric_id="reynolds_number", value=10000.0, unit="dimensionless")
        interp = analyzer._interpret_metric(result)
        assert interp is not None
        assert "湍流" in interp

    def test_interpret_strouhal_in_range(self):
        """Strouhal in 0.15-0.25 is typical vortex shedding."""
        analyzer = ScientificAnalyzer()
        result = MetricResult(metric_id="strouhal_number", value=0.2, unit="dimensionless")
        interp = analyzer._interpret_metric(result)
        assert interp is not None
        assert "典型涡脱范围" in interp

    def test_interpret_strouhal_out_of_range(self):
        """Strouhal outside 0.15-0.25 is atypical."""
        analyzer = ScientificAnalyzer()
        result = MetricResult(metric_id="strouhal_number", value=0.5, unit="dimensionless")
        interp = analyzer._interpret_metric(result)
        assert interp is not None
        assert "偏离" in interp

    def test_interpret_pressure_drop(self):
        """Pressure drop interpretation."""
        analyzer = ScientificAnalyzer()
        result = MetricResult(metric_id="pressure_drop", value=150.0, unit="Pa")
        interp = analyzer._interpret_metric(result)
        assert interp is not None
        assert "压降" in interp

    def test_interpret_unknown_metric_returns_none(self):
        """Unknown metric returns None."""
        analyzer = ScientificAnalyzer()
        result = MetricResult(metric_id="unknown_metric", value=1.0, unit="")
        interp = analyzer._interpret_metric(result)
        assert interp is None


# --------------------------------------------------------------------------- #
# AnalysisLayer model
# --------------------------------------------------------------------------- #


class TestAnalysisLayerModel:
    """Test AnalysisLayer pydantic model."""

    def test_defaults(self):
        layer = AnalysisLayer(layer_name="test", content="test content")
        assert layer.layer_name == "test"
        assert layer.content == "test content"
        assert layer.evidence_ids == []
        assert layer.confidence == "high"

    def test_with_evidence(self):
        layer = AnalysisLayer(
            layer_name="test",
            content="test content",
            evidence_ids=["metric:pressure_drop"],
            confidence="medium",
        )
        assert layer.evidence_ids == ["metric:pressure_drop"]
        assert layer.confidence == "medium"
