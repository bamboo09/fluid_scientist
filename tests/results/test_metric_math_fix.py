"""Tests for Commit 6: Fix metric math calculations.

These tests verify that:
1. Pressure drop uses time-averaged values (not last value) with std/CI
2. Pressure drop discards initial transient
3. Strouhal number uses the real time column from postProcessing
4. Strouhal falls back to parameter dt with a warning when no time column
5. Velocity uniformity uses proper CV = std/mean (not the old approximation)
6. Drag coefficient uses time-averaged value (not last value)
7. Friction factor uses time-averaged pressure drop
8. The _time_averaged_stats helper computes correct statistics
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from fluid_scientist.results.metric_executor import (
    MetricExecutor,
    _time_averaged_stats,
)
from fluid_scientist.results.models import SimulationData

# --------------------------------------------------------------------------- #
# Helper: create synthetic Strouhal data with a real time column
# --------------------------------------------------------------------------- #


def _make_strouhal_data_with_time(
    frequency: float = 10.0,
    n_samples: int = 200,
    dt: float = 0.01,
    amplitude: float = 0.5,
    time_object_name: str = "forceCoeffs",
) -> SimulationData:
    """Create SimulationData with a synthetic Cl time series and a real time column."""
    t = np.arange(n_samples) * dt
    cl = amplitude * np.sin(2 * math.pi * frequency * t)
    rng = np.random.default_rng(42)
    cl = cl + 0.001 * rng.standard_normal(n_samples)
    return SimulationData(
        force_coefficients={"Cl": cl.tolist(), "Cd": [0.0] * n_samples},
        time_values={time_object_name: t.tolist()},
    )


# --------------------------------------------------------------------------- #
# 1. Pressure drop — time-averaged
# --------------------------------------------------------------------------- #


class TestPressureDropTimeAveraged:
    """B4-5: Pressure drop must use time-averaged values, not the last value."""

    def test_pressure_drop_time_averaged(self):
        """Result is the mean of the steady-state portion, not the last value."""
        n = 20
        inlet = [100.0 + 10.0 * math.sin(2 * math.pi * i / n) for i in range(n)]
        outlet = [20.0 + 5.0 * math.sin(2 * math.pi * i / n) for i in range(n)]
        data = SimulationData(
            surface_field_values={
                "pressure_inlet_average": inlet,
                "pressure_outlet_average": outlet,
            },
        )
        executor = MetricExecutor()
        result = executor.execute("pressure_drop", data)

        assert result.value is not None
        # Compute expected using the same helper
        in_mean, in_std, in_ci = _time_averaged_stats(inlet)
        out_mean, out_std, out_ci = _time_averaged_stats(outlet)
        expected_dp = in_mean - out_mean
        assert result.value == pytest.approx(expected_dp)

        # Should NOT equal the last-value difference
        last_dp = inlet[-1] - outlet[-1]
        assert result.value != pytest.approx(last_dp, rel=0.01)

        # Statistics should include std and CI
        assert "inlet" in result.statistics
        assert "outlet" in result.statistics
        assert result.statistics["inlet"]["std"] > 0
        assert result.statistics["inlet"]["ci_95"] > 0
        assert result.statistics["outlet"]["std"] > 0
        assert result.statistics["outlet"]["ci_95"] > 0


# --------------------------------------------------------------------------- #
# 2. Pressure drop — discards transient
# --------------------------------------------------------------------------- #


class TestPressureDropDiscardsTransient:
    """The initial transient must be discarded before averaging."""

    def test_pressure_drop_discards_transient(self):
        """First 20% of values are transient and should not affect the mean."""
        # First 4 values are transient (very different), rest steady
        inlet = [500.0] * 4 + [100.0] * 16
        outlet = [200.0] * 4 + [20.0] * 16
        data = SimulationData(
            surface_field_values={
                "pressure_inlet_average": inlet,
                "pressure_outlet_average": outlet,
            },
        )
        executor = MetricExecutor()
        result = executor.execute("pressure_drop", data)

        # With discard_fraction=0.2, start_idx=4, steady = [100]*16 and [20]*16
        # dp = 100 - 20 = 80
        assert result.value == pytest.approx(80.0)

        # If transient were NOT discarded, the mean would be different
        full_mean_inlet = sum(inlet) / len(inlet)  # (500*4 + 100*16)/20 = 180
        full_mean_outlet = sum(outlet) / len(outlet)  # (200*4 + 20*16)/20 = 56
        full_dp = full_mean_inlet - full_mean_outlet  # = 124
        assert result.value != pytest.approx(full_dp)


# --------------------------------------------------------------------------- #
# 3. Strouhal — uses real time column
# --------------------------------------------------------------------------- #


class TestStrouhalRealTimeColumn:
    """B4-6: Strouhal must use the real time column, not the parameter dt."""

    def test_strouhal_uses_real_time_column(self):
        """Frequency is computed using the real dt from time_values."""
        frequency = 10.0  # Hz
        dt_real = 0.005  # real dt (fs = 200 Hz)
        n_samples = 200
        diameter = 0.1
        velocity = 1.0

        data = _make_strouhal_data_with_time(
            frequency=frequency,
            n_samples=n_samples,
            dt=dt_real,
            time_object_name="forceCoeffs",
        )
        executor = MetricExecutor()
        # Pass a WRONG dt in parameters — the code should ignore it
        result = executor.execute(
            "strouhal_number",
            data,
            parameters={
                "diameter": diameter,
                "mean_velocity": velocity,
                "time_step": 0.01,  # wrong dt
            },
        )

        expected_st = frequency * diameter / velocity  # = 1.0
        assert result.value is not None
        assert result.value == pytest.approx(expected_st, rel=0.05)

        # If the code had used the wrong dt (0.01), the detected frequency
        # would be halved (5 Hz instead of 10 Hz), giving St = 0.5
        wrong_st = (frequency / 2) * diameter / velocity  # = 0.5
        assert result.value != pytest.approx(wrong_st, rel=0.05)

        # Should confirm real time column was used
        assert result.statistics.get("used_real_time_column") is True


# --------------------------------------------------------------------------- #
# 4. Strouhal — falls back to parameter dt
# --------------------------------------------------------------------------- #


class TestStrouhalFallbackParameterDt:
    """When no time_values are available, fall back to parameter dt with a warning."""

    def test_strouhal_falls_back_to_parameter_dt(self):
        """Without time_values, parameter dt is used and a warning is issued."""
        frequency = 10.0
        dt = 0.01
        n_samples = 200

        # Create data WITHOUT time_values
        t = np.arange(n_samples) * dt
        cl = 0.5 * np.sin(2 * math.pi * frequency * t)
        rng = np.random.default_rng(42)
        cl = cl + 0.001 * rng.standard_normal(n_samples)
        data = SimulationData(
            force_coefficients={"Cl": cl.tolist(), "Cd": [0.0] * n_samples},
        )

        executor = MetricExecutor()
        result = executor.execute(
            "strouhal_number",
            data,
            parameters={
                "diameter": 0.1,
                "mean_velocity": 1.0,
                "time_step": dt,
            },
        )

        expected_st = frequency * 0.1 / 1.0  # = 1.0
        assert result.value is not None
        assert result.value == pytest.approx(expected_st, rel=0.05)

        # Should have a warning about the fallback
        assert any("时间" in w or "time" in w.lower() for w in result.warnings)

        # Should confirm real time column was NOT used
        assert result.statistics.get("used_real_time_column") is False


# --------------------------------------------------------------------------- #
# 5. Velocity uniformity — proper CV
# --------------------------------------------------------------------------- #


class TestVelocityUniformityProperCv:
    """B4-7: Velocity uniformity must use CV = std/mean, not the old approximation."""

    def test_velocity_uniformity_proper_cv(self):
        """CV is std(u)/|mean(u)| from the time series, not mag^2 - mean^2."""
        vel = [1.0, 1.1, 0.9, 1.05, 0.95, 1.0, 1.02, 0.98, 1.01, 0.99]
        data = SimulationData(
            surface_field_values={"velocity_mean_outlet": vel},
        )
        executor = MetricExecutor()
        result = executor.execute("velocity_uniformity", data)

        assert result.value is not None
        mean_u, std_u, _ = _time_averaged_stats(vel)
        expected_cv = std_u / abs(mean_u)
        assert result.value == pytest.approx(expected_cv, rel=0.01)

        # There IS variation, so CV should be positive
        assert result.value > 0

        # The old approximation would give a different answer:
        # With only mean data (no magnitude), old code used time variation
        # of last 5 values. The new code uses the full steady-state portion.
        # Verify the result is NOT zero (which single-value data would give).
        assert result.value != pytest.approx(0.0, abs=1e-10)

        # Statistics should be present
        assert result.statistics["cv"] == pytest.approx(expected_cv, rel=0.01)
        assert result.statistics["n_samples"] == len(vel)


# --------------------------------------------------------------------------- #
# 6. Drag coefficient — time-averaged
# --------------------------------------------------------------------------- #


class TestDragCoefficientTimeAveraged:
    """B4-8: Drag coefficient must use time-averaged value, not the last value."""

    def test_drag_coefficient_time_averaged(self):
        """Result is the mean of Cd, not the last value."""
        # Cd oscillates so the last value differs from the mean
        n = 20
        cd = [1.0 + 0.2 * math.sin(2 * math.pi * i / n) for i in range(n)]
        data = SimulationData(
            force_coefficients={"Cd": cd, "Cl": [0.0] * n},
        )
        executor = MetricExecutor()
        result = executor.execute("drag_coefficient", data)

        assert result.value is not None
        cd_mean, cd_std, cd_ci = _time_averaged_stats(cd)
        assert result.value == pytest.approx(cd_mean)

        # Should NOT be the last value
        assert result.value != pytest.approx(cd[-1], rel=0.01)

        # Statistics should include mean, std, ci
        assert result.statistics["mean"] == pytest.approx(cd_mean)
        assert result.statistics["std"] == pytest.approx(cd_std)
        assert result.statistics["ci_95"] == pytest.approx(cd_ci)
        assert result.statistics["n_samples"] == n


# --------------------------------------------------------------------------- #
# 7. Friction factor — time-averaged pressure drop
# --------------------------------------------------------------------------- #


class TestFrictionFactorTimeAveraged:
    """B4-9: Friction factor must use time-averaged pressure drop."""

    def test_friction_factor_time_averaged(self):
        """Friction factor uses the mean pressure drop, not the last value."""
        n = 20
        inlet = [110.0 + 10.0 * math.sin(2 * math.pi * i / n) for i in range(n)]
        outlet = [10.0 + 5.0 * math.sin(2 * math.pi * i / n) for i in range(n)]
        data = SimulationData(
            surface_field_values={
                "pressure_inlet_average": inlet,
                "pressure_outlet_average": outlet,
            },
        )
        executor = MetricExecutor()
        params = {
            "density": 1000.0,
            "mean_velocity": 0.1,
            "diameter": 0.05,
            "length": 1.0,
        }
        result = executor.execute("friction_factor", data, parameters=params)

        assert result.value is not None
        in_mean, _, _ = _time_averaged_stats(inlet)
        out_mean, _, _ = _time_averaged_stats(outlet)
        dp = in_mean - out_mean
        expected_f = dp / (0.5 * 1000.0 * 0.1**2 * 1.0 / 0.05)
        assert result.value == pytest.approx(expected_f)

        # Should NOT match the last-value-based friction factor
        last_dp = inlet[-1] - outlet[-1]
        last_f = last_dp / (0.5 * 1000.0 * 0.1**2 * 1.0 / 0.05)
        assert result.value != pytest.approx(last_f, rel=0.01)

        # Statistics should include the time-averaged dp
        assert result.statistics["dp_mean"] == pytest.approx(dp)


# --------------------------------------------------------------------------- #
# 8. _time_averaged_stats helper — direct test
# --------------------------------------------------------------------------- #


class TestTimeAveragedStatsHelper:
    """Direct test of the _time_averaged_stats() function with known values."""

    def test_known_values(self):
        """Verify mean, std, and CI for a known dataset."""
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        # discard_fraction=0.2, start_idx = int(5 * 0.2) = 1
        # steady = [2, 3, 4, 5], m = 4
        # mean = 14 / 4 = 3.5
        # variance = ((2-3.5)^2 + (3-3.5)^2 + (4-3.5)^2 + (5-3.5)^2) / 3
        #         = (2.25 + 0.25 + 0.25 + 2.25) / 3 = 5/3
        # std = sqrt(5/3)
        # ci = 1.96 * std / sqrt(4)
        mean, std, ci = _time_averaged_stats(values)

        assert mean == pytest.approx(3.5)
        assert std == pytest.approx(math.sqrt(5.0 / 3.0), rel=1e-4)
        assert ci == pytest.approx(1.96 * math.sqrt(5.0 / 3.0) / 2.0, rel=1e-4)

    def test_empty_values(self):
        """Empty input returns zeros."""
        mean, std, ci = _time_averaged_stats([])
        assert mean == 0.0
        assert std == 0.0
        assert ci == 0.0

    def test_single_value(self):
        """Single value has std=0 and ci=0."""
        mean, std, ci = _time_averaged_stats([42.0])
        assert mean == 42.0
        assert std == 0.0
        assert ci == 0.0

    def test_constant_values(self):
        """Constant values have std=0."""
        mean, std, ci = _time_averaged_stats([5.0] * 20)
        assert mean == 5.0
        assert std == 0.0
        assert ci == 0.0

    def test_discard_fraction(self):
        """Increasing discard_fraction changes which values are included."""
        # With discard_fraction=0, all values are included
        values = [0.0, 0.0, 0.0, 10.0, 10.0]
        mean0, _, _ = _time_averaged_stats(values, discard_fraction=0.0)
        # start_idx = 0, steady = all, mean = 20/5 = 4.0
        assert mean0 == pytest.approx(4.0)

        # With discard_fraction=0.6, start_idx = int(5*0.6) = 3
        # steady = [10.0, 10.0], mean = 10.0
        mean06, _, _ = _time_averaged_stats(values, discard_fraction=0.6)
        assert mean06 == pytest.approx(10.0)
