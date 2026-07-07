"""Metric Executor — calculates metrics from SimulationData deterministically.

All metric calculations are performed by deterministic programs, not LLM.
The executor consumes SimulationData (from ResultIngestor) and produces
MetricResult objects with quality checks.

Supported metrics:
- pressure_drop: p_inlet - p_outlet (from surfaceFieldValue)
- drag_coefficient: Cd from forceCoeffs
- lift_coefficient: Cl from forceCoeffs
- strouhal_number: f * D / U (from FFT of Cl or U probe)
- velocity_uniformity: CV = sigma_u / mean_u
- reynolds_number: rho * U * D / mu
- friction_factor: dp / (0.5 * rho * U^2 * L / D)
- mass_flow_rate: rho * U * A
- residual_tolerance: max(final residuals)
- max_courant: max Courant number from log
"""

from __future__ import annotations

import math
from typing import Any

from fluid_scientist.results.models import MetricResult, SimulationData


def _time_averaged_stats(
    values: list[float], discard_fraction: float = 0.2
) -> tuple[float, float, float]:
    """Compute time-averaged statistics, discarding initial transient.

    Args:
        values: Time series of values.
        discard_fraction: Fraction of initial values to discard (transient period).

    Returns:
        (mean, std, confidence_interval_95)
    """
    n = len(values)
    if n == 0:
        return (0.0, 0.0, 0.0)
    start_idx = int(n * discard_fraction)
    if start_idx >= n - 1:
        start_idx = 0
    steady = values[start_idx:]
    m = len(steady)
    mean_val = sum(steady) / m
    if m > 1:
        variance = sum((v - mean_val) ** 2 for v in steady) / (m - 1)
        std_val = math.sqrt(variance)
        # 95% confidence interval (t-distribution approximated with z=1.96 for large samples)
        ci_95 = 1.96 * std_val / math.sqrt(m)
    else:
        std_val = 0.0
        ci_95 = 0.0
    return (mean_val, std_val, ci_95)


class QualityCheckResult:
    """Result of a single quality check."""
    def __init__(self, name: str, passed: bool, message: str = "") -> None:
        self.name = name
        self.passed = passed
        self.message = message

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "message": self.message}


class MetricExecutor:
    """Executes metric calculations from SimulationData.

    All calculations are deterministic — no LLM estimation.
    """

    def execute(
        self,
        metric_id: str,
        simulation_data: SimulationData,
        metric_definition: dict[str, Any] | None = None,
        parameters: dict[str, Any] | None = None,
    ) -> MetricResult:
        """Execute a single metric calculation.

        Args:
            metric_id: The metric to calculate.
            simulation_data: Parsed simulation data.
            metric_definition: Optional metric definition with formula, unit.
            parameters: Optional spec parameters (diameter, velocity, etc.).

        Returns:
            MetricResult with value, quality_checks, and confidence.
        """
        parameters = parameters or {}

        # Check if required data is available
        if metric_id in simulation_data.missing_data:
            return MetricResult(
                metric_id=metric_id,
                value=None,
                confidence="failed",
                data_missing=True,
                missing_reason=f"Required data '{metric_id}' not found in simulation results",
                warnings=[f"建议重新编译和运行以获取 {metric_id} 数据"],
            )

        # Dispatch to specific calculator
        if metric_id == "pressure_drop":
            return self._calc_pressure_drop(simulation_data, metric_definition)
        elif metric_id == "drag_coefficient":
            return self._calc_drag_coefficient(simulation_data, metric_definition)
        elif metric_id == "lift_coefficient":
            return self._calc_lift_coefficient(simulation_data, metric_definition)
        elif metric_id == "strouhal_number":
            return self._calc_strouhal_number(simulation_data, metric_definition, parameters)
        elif metric_id == "velocity_uniformity" or metric_id == "outlet_velocity_uniformity":
            return self._calc_velocity_uniformity(simulation_data, metric_definition)
        elif metric_id == "reynolds_number":
            return self._calc_reynolds_number(simulation_data, metric_definition, parameters)
        elif metric_id == "friction_factor":
            return self._calc_friction_factor(simulation_data, metric_definition, parameters)
        elif metric_id == "mass_flow_rate":
            return self._calc_mass_flow_rate(simulation_data, metric_definition, parameters)
        elif metric_id == "residual_tolerance":
            return self._calc_residual_tolerance(simulation_data, metric_definition)
        elif metric_id == "max_courant":
            return self._calc_max_courant(simulation_data, metric_definition)
        else:
            return MetricResult(
                metric_id=metric_id,
                value=None,
                confidence="failed",
                data_missing=True,
                missing_reason=f"No calculator implemented for metric '{metric_id}'",
            )

    def execute_all(
        self,
        metric_ids: list[str],
        simulation_data: SimulationData,
        metric_definitions: dict[str, dict[str, Any]] | None = None,
        parameters: dict[str, Any] | None = None,
    ) -> list[MetricResult]:
        """Execute multiple metrics."""
        metric_definitions = metric_definitions or {}
        results = []
        for mid in metric_ids:
            mdef = metric_definitions.get(mid)
            result = self.execute(mid, simulation_data, mdef, parameters)
            results.append(result)
        return results

    # --- Specific metric calculators ---

    def _calc_pressure_drop(
        self,
        data: SimulationData,
        mdef: dict[str, Any] | None,
    ) -> MetricResult:
        """Calculate pressure drop: p_inlet - p_outlet using time-averaged values."""
        quality_checks: list[dict[str, Any]] = []
        warnings: list[str] = []

        # Look for inlet and outlet surface field values
        inlet_key = None
        outlet_key = None
        for key in data.surface_field_values:
            if "inlet" in key.lower():
                inlet_key = key
            elif "outlet" in key.lower():
                outlet_key = key

        if inlet_key is None or outlet_key is None:
            return MetricResult(
                metric_id="pressure_drop",
                value=None,
                unit="Pa",
                confidence="failed",
                data_missing=True,
                missing_reason="Need both inlet and outlet pressure surface field values",
            )

        inlet_vals = data.surface_field_values[inlet_key]
        outlet_vals = data.surface_field_values[outlet_key]

        if not inlet_vals or not outlet_vals:
            return MetricResult(
                metric_id="pressure_drop",
                value=None,
                unit="Pa",
                confidence="failed",
                data_missing=True,
                missing_reason="Empty pressure values",
            )

        # Time-averaged statistics (discard initial transient)
        inlet_mean, inlet_std, inlet_ci = _time_averaged_stats(inlet_vals)
        outlet_mean, outlet_std, outlet_ci = _time_averaged_stats(outlet_vals)
        pressure_drop = inlet_mean - outlet_mean

        # Quality check: steady state achieved (CV < threshold)
        inlet_cv = inlet_std / max(abs(inlet_mean), 1e-10)
        outlet_cv = outlet_std / max(abs(outlet_mean), 1e-10)
        steady_passed = inlet_cv < 0.05 and outlet_cv < 0.05
        check = QualityCheckResult(
            "steady_state_achieved",
            steady_passed,
            f"inlet CV: {inlet_cv:.4f}, outlet CV: {outlet_cv:.4f}",
        )
        quality_checks.append(check.to_dict())
        if not check.passed:
            warnings.append("压力未达到稳态，建议增加仿真时间")

        statistics = {
            "inlet": {
                "mean": inlet_mean,
                "std": inlet_std,
                "ci_95": inlet_ci,
                "n_samples": len(inlet_vals),
            },
            "outlet": {
                "mean": outlet_mean,
                "std": outlet_std,
                "ci_95": outlet_ci,
                "n_samples": len(outlet_vals),
            },
            "pressure_drop": pressure_drop,
            "discard_fraction": 0.2,
        }

        return MetricResult(
            metric_id="pressure_drop",
            value=pressure_drop,
            unit="Pa",
            quality_checks=quality_checks,
            confidence="high" if all(q["passed"] for q in quality_checks) else "medium",
            warnings=warnings,
            statistics=statistics,
        )

    def _calc_drag_coefficient(
        self,
        data: SimulationData,
        mdef: dict[str, Any] | None,
    ) -> MetricResult:
        """Extract time-averaged Cd from forceCoeffs data."""
        if "Cd" not in data.force_coefficients or not data.force_coefficients["Cd"]:
            return MetricResult(
                metric_id="drag_coefficient",
                value=None,
                confidence="failed",
                data_missing=True,
                missing_reason="No Cd data in forceCoeffs output",
            )

        cd_values = data.force_coefficients["Cd"]
        cd_mean, cd_std, cd_ci = _time_averaged_stats(cd_values)

        quality_checks = []
        # Statistical stability check
        cv = cd_std / max(abs(cd_mean), 1e-10)
        check = QualityCheckResult(
            "cd_statistical_stability",
            cv < 0.1,
            f"CV of steady-state Cd: {cv:.4f}",
        )
        quality_checks.append(check.to_dict())

        statistics = {
            "mean": cd_mean,
            "std": cd_std,
            "ci_95": cd_ci,
            "n_samples": len(cd_values),
            "discard_fraction": 0.2,
        }

        return MetricResult(
            metric_id="drag_coefficient",
            value=cd_mean,
            unit="dimensionless",
            quality_checks=quality_checks,
            confidence="high" if all(q["passed"] for q in quality_checks) else "medium",
            statistics=statistics,
        )

    def _calc_lift_coefficient(
        self,
        data: SimulationData,
        mdef: dict[str, Any] | None,
    ) -> MetricResult:
        """Extract time-averaged Cl from forceCoeffs data."""
        if "Cl" not in data.force_coefficients or not data.force_coefficients["Cl"]:
            return MetricResult(
                metric_id="lift_coefficient",
                value=None,
                confidence="failed",
                data_missing=True,
                missing_reason="No Cl data in forceCoeffs output",
            )

        cl_values = data.force_coefficients["Cl"]
        cl_mean, cl_std, cl_ci = _time_averaged_stats(cl_values)

        statistics = {
            "mean": cl_mean,
            "std": cl_std,
            "ci_95": cl_ci,
            "n_samples": len(cl_values),
            "discard_fraction": 0.2,
        }

        return MetricResult(
            metric_id="lift_coefficient",
            value=cl_mean,
            unit="dimensionless",
            confidence="high",
            statistics=statistics,
        )

    def _calc_strouhal_number(
        self,
        data: SimulationData,
        mdef: dict[str, Any] | None,
        parameters: dict[str, Any],
    ) -> MetricResult:
        """Calculate Strouhal number: St = f * D / U.

        Requires spectral analysis of Cl or U probe data to find shedding frequency.
        Uses the real time column from postProcessing when available.
        """
        quality_checks: list[dict[str, Any]] = []
        warnings: list[str] = []

        # Need Cl time series for FFT
        cl_values = data.force_coefficients.get("Cl", [])

        if len(cl_values) < 20:
            return MetricResult(
                metric_id="strouhal_number",
                value=None,
                confidence="failed",
                data_missing=True,
                missing_reason=f"Need at least 20 Cl samples for FFT, got {len(cl_values)}",
            )

        # Quality checks for spectral analysis
        # 1. Data length
        check1 = QualityCheckResult(
            "data_length",
            len(cl_values) >= 100,
            f"Data length: {len(cl_values)} (recommended: >=100)",
        )
        quality_checks.append(check1.to_dict())
        if not check1.passed:
            warnings.append("数据长度不足，频率分辨率可能不够")

        # Determine dt from real time column if available
        time_column = None
        if data.time_values:
            # Try common functionObject names for force coefficients
            for candidate_key in (
                "forceCoeffs",
                "force_coeffs",
                "forces",
                "forceCoefficients",
            ):
                if candidate_key in data.time_values:
                    time_column = data.time_values[candidate_key]
                    break
            # If only one entry exists, use it
            if time_column is None and len(data.time_values) == 1:
                time_column = next(iter(data.time_values.values()))

        used_real_time = False
        if time_column is not None and len(time_column) >= 2:
            # Compute average dt from the actual time column
            dt = (time_column[-1] - time_column[0]) / (len(time_column) - 1)
            used_real_time = True
            # 2. Time step uniformity — verify the time column is uniform
            if len(time_column) > 2:
                sample_dts = [
                    time_column[i + 1] - time_column[i]
                    for i in range(min(len(time_column) - 1, 20))
                ]
                dt_max_dev = (
                    max(abs(d - dt) for d in sample_dts) / max(abs(dt), 1e-10)
                )
                check2 = QualityCheckResult(
                    "time_step_uniformity",
                    dt_max_dev < 0.01,
                    f"Max dt deviation: {dt_max_dev * 100:.2f}%",
                )
                quality_checks.append(check2.to_dict())
            else:
                check2 = QualityCheckResult(
                    "time_step_uniformity",
                    True,
                    "Insufficient time data to verify uniformity",
                )
                quality_checks.append(check2.to_dict())
        else:
            # Fall back to parameter dt
            dt = parameters.get("time_step", parameters.get("interval", 0.01))
            warnings.append(
                "未找到真实时间列 (time_values)，使用参数 time_step 作为时间步长"
            )
            check2 = QualityCheckResult(
                "time_step_uniformity",
                True,
                "Using parameter dt (assumed uniform)",
            )
            quality_checks.append(check2.to_dict())

        # 3. Signal stationarity (check if mean is stable in first vs second half)
        n = len(cl_values)
        half = n // 2
        mean1 = sum(cl_values[:half]) / max(half, 1)
        mean2 = sum(cl_values[half:]) / max(n - half, 1)
        rel_diff = abs(mean2 - mean1) / max(abs(mean1), abs(mean2), 1e-10)
        check3 = QualityCheckResult(
            "signal_stationarity",
            rel_diff < 0.2,
            f"Relative mean difference: {rel_diff:.4f}",
        )
        quality_checks.append(check3.to_dict())
        if not check3.passed:
            warnings.append("信号非平稳，频谱分析结果可能不可靠")

        # Perform spectral analysis to find dominant frequency
        N = len(cl_values)
        # Remove mean (detrend)
        mean_cl = sum(cl_values) / N
        detrended = [v - mean_cl for v in cl_values]

        frequency = 0.0
        spectral_method = "none"
        try:
            import numpy as np

            # Convert to numpy array (scipy.signal.welch requires ndarray)
            detrended_np = np.array(detrended)

            try:
                from scipy.signal import welch

                fs = 1.0 / dt
                freqs, psd = welch(detrended_np, fs=fs)
                # Skip DC component (index 0)
                if len(psd) > 1:
                    peak_idx = 1 + int(np.argmax(psd[1:]))
                else:
                    peak_idx = 0
                frequency = float(freqs[peak_idx])
                magnitudes = psd
                spectral_method = "welch"
            except ImportError:
                # Fallback to numpy FFT
                fft_result = np.fft.rfft(detrended_np)
                magnitudes = np.abs(fft_result)
                peak_idx = 1 + int(np.argmax(magnitudes[1:]))
                frequency = peak_idx / (N * dt)
                spectral_method = "fft"

            # 4. Peak prominence
            if len(magnitudes) > peak_idx + 2:
                local_mean = float(np.mean(magnitudes[1:]))
                prominence = float(magnitudes[peak_idx]) / max(local_mean, 1e-10)
                check4 = QualityCheckResult(
                    "peak_prominence",
                    prominence > 5.0,
                    f"Peak prominence ratio: {prominence:.1f}",
                )
                quality_checks.append(check4.to_dict())
                if not check4.passed:
                    warnings.append("峰值不显著，可能是噪声而非真实涡脱频率")
            else:
                check4 = QualityCheckResult(
                    "peak_prominence",
                    False,
                    "Insufficient data for prominence check",
                )
                quality_checks.append(check4.to_dict())

            # 5. Frequency resolution
            freq_resolution = 1.0 / (N * dt)
            check5 = QualityCheckResult(
                "frequency_resolution",
                freq_resolution < frequency * 0.1,
                f"Resolution: {freq_resolution:.4f} Hz, Frequency: {frequency:.4f} Hz",
            )
            quality_checks.append(check5.to_dict())

            # 6. Statistical cycles
            period = 1.0 / frequency if frequency > 0 else float('inf')
            total_time = N * dt
            num_cycles = total_time / period if period > 0 else 0
            check6 = QualityCheckResult(
                "statistical_cycles",
                num_cycles >= 10,
                f"Number of cycles: {num_cycles:.1f} (recommended: >=10)",
            )
            quality_checks.append(check6.to_dict())
            if not check6.passed:
                warnings.append(f"统计周期数不足 ({num_cycles:.1f} < 10)")
        except ImportError:
            # numpy not available — cannot perform spectral analysis
            warnings.append("numpy not available, FFT quality checks skipped")

        # Calculate Strouhal
        diameter = parameters.get("diameter", 0.1)
        velocity = parameters.get("mean_velocity", parameters.get("inlet_velocity", 1.0))

        if velocity <= 0:
            return MetricResult(
                metric_id="strouhal_number",
                value=None,
                confidence="failed",
                data_missing=True,
                missing_reason="Velocity parameter is zero or missing",
            )

        strouhal = frequency * diameter / velocity

        statistics = {
            "frequency": frequency,
            "dt": dt,
            "used_real_time_column": used_real_time,
            "spectral_method": spectral_method,
            "n_samples": N,
        }

        all_passed = all(q["passed"] for q in quality_checks)
        passed_count = sum(1 for q in quality_checks if q["passed"])
        confidence = "high" if all_passed else ("medium" if passed_count >= 4 else "low")

        return MetricResult(
            metric_id="strouhal_number",
            value=strouhal,
            unit="dimensionless",
            quality_checks=quality_checks,
            confidence=confidence,
            warnings=warnings,
            statistics=statistics,
        )

    def _calc_velocity_uniformity(
        self,
        data: SimulationData,
        mdef: dict[str, Any] | None,
    ) -> MetricResult:
        """Calculate velocity uniformity: CV = std(u) / |mean(u)|.

        Uses proper time-averaged coefficient of variation computed over
        the steady-state portion of the velocity time series.
        """
        # Look for velocity mean time series
        mean_u_key = None
        for key in data.surface_field_values:
            if "velocity" in key.lower() and "mean" in key.lower():
                mean_u_key = key

        if mean_u_key is None:
            return MetricResult(
                metric_id="velocity_uniformity",
                value=None,
                confidence="failed",
                data_missing=True,
                missing_reason="No velocity mean surface field value found",
            )

        mean_u_vals = data.surface_field_values[mean_u_key]
        if not mean_u_vals:
            return MetricResult(
                metric_id="velocity_uniformity",
                value=None,
                confidence="failed",
                data_missing=True,
                missing_reason="Empty velocity mean values",
            )

        # Time-averaged statistics (discard initial transient)
        mean_u, std_u, ci_u = _time_averaged_stats(mean_u_vals)

        if abs(mean_u) < 1e-10:
            return MetricResult(
                metric_id="velocity_uniformity",
                value=None,
                confidence="failed",
                missing_reason="Mean velocity is zero, cannot calculate CV",
            )

        # CV = std(u) / |mean(u)|
        cv = std_u / abs(mean_u)

        # Quality check: sufficient samples for statistical reliability
        n_samples = len(mean_u_vals)
        check = QualityCheckResult(
            "sufficient_samples",
            n_samples >= 10,
            f"Samples: {n_samples} (recommended: >=10)",
        )
        quality_checks = [check.to_dict()]

        statistics = {
            "mean": mean_u,
            "std": std_u,
            "ci_95": ci_u,
            "cv": cv,
            "n_samples": n_samples,
            "discard_fraction": 0.2,
        }

        return MetricResult(
            metric_id="velocity_uniformity",
            value=cv,
            unit="dimensionless",
            quality_checks=quality_checks,
            confidence="high" if n_samples >= 10 else "medium",
            statistics=statistics,
        )

    def _calc_reynolds_number(
        self,
        data: SimulationData,
        mdef: dict[str, Any] | None,
        parameters: dict[str, Any],
    ) -> MetricResult:
        """Calculate Reynolds number: Re = rho * U * D / mu."""
        rho = parameters.get("density", 998.2)
        velocity = parameters.get("mean_velocity", parameters.get("inlet_velocity", 0))
        diameter = parameters.get("diameter", 0.05)
        viscosity = parameters.get("kinematic_viscosity", 1e-6)

        if viscosity == 0:
            return MetricResult(
                metric_id="reynolds_number",
                value=None,
                confidence="failed",
                missing_reason="Kinematic viscosity is zero",
            )

        re = rho * velocity * diameter / (viscosity * rho)  # Re = U * D / nu
        # Actually Re = U * D / nu (kinematic viscosity already includes rho)
        re = velocity * diameter / viscosity

        return MetricResult(
            metric_id="reynolds_number",
            value=re,
            unit="dimensionless",
            confidence="high",
        )

    def _calc_friction_factor(
        self,
        data: SimulationData,
        mdef: dict[str, Any] | None,
        parameters: dict[str, Any],
    ) -> MetricResult:
        """Calculate friction factor: f = dp / (0.5 * rho * U^2 * L / D).

        Uses time-averaged pressure drop from surface field values.
        """
        # Get pressure drop from surface field values
        inlet_key = None
        outlet_key = None
        for key in data.surface_field_values:
            if "inlet" in key.lower():
                inlet_key = key
            elif "outlet" in key.lower():
                outlet_key = key

        if inlet_key is None or outlet_key is None:
            return MetricResult(
                metric_id="friction_factor",
                value=None,
                confidence="failed",
                data_missing=True,
                missing_reason="Need inlet and outlet pressure for friction factor",
            )

        inlet_vals = data.surface_field_values[inlet_key]
        outlet_vals = data.surface_field_values[outlet_key]

        if not inlet_vals or not outlet_vals:
            return MetricResult(
                metric_id="friction_factor",
                value=None,
                confidence="failed",
                data_missing=True,
                missing_reason="Empty pressure values",
            )

        # Time-averaged pressure drop
        inlet_mean, inlet_std, inlet_ci = _time_averaged_stats(inlet_vals)
        outlet_mean, outlet_std, outlet_ci = _time_averaged_stats(outlet_vals)
        dp = inlet_mean - outlet_mean

        rho = parameters.get("density", 998.2)
        velocity = parameters.get("mean_velocity", 0.1)
        diameter = parameters.get("diameter", 0.05)
        length = parameters.get("length", 1.0)

        if velocity == 0 or diameter == 0:
            return MetricResult(
                metric_id="friction_factor",
                value=None,
                confidence="failed",
                missing_reason="Velocity or diameter is zero",
            )

        f = dp / (0.5 * rho * velocity**2 * length / diameter)

        # Propagate std for pressure drop (assuming independence)
        dp_std = math.sqrt(inlet_std**2 + outlet_std**2)

        statistics = {
            "dp_mean": dp,
            "dp_std": dp_std,
            "inlet_mean": inlet_mean,
            "outlet_mean": outlet_mean,
            "n_samples": min(len(inlet_vals), len(outlet_vals)),
            "discard_fraction": 0.2,
        }

        return MetricResult(
            metric_id="friction_factor",
            value=f,
            unit="dimensionless",
            confidence="high",
            statistics=statistics,
        )

    def _calc_mass_flow_rate(
        self,
        data: SimulationData,
        mdef: dict[str, Any] | None,
        parameters: dict[str, Any],
    ) -> MetricResult:
        """Calculate mass flow rate: m_dot = rho * U * A."""
        rho = parameters.get("density", 998.2)
        velocity = parameters.get("mean_velocity", parameters.get("inlet_velocity", 0))
        diameter = parameters.get("diameter", 0.05)

        area = math.pi * (diameter / 2) ** 2
        m_dot = rho * velocity * area

        return MetricResult(
            metric_id="mass_flow_rate",
            value=m_dot,
            unit="kg/s",
            confidence="high",
        )

    def _calc_residual_tolerance(
        self,
        data: SimulationData,
        mdef: dict[str, Any] | None,
    ) -> MetricResult:
        """Get max final residual."""
        if not data.final_residuals:
            return MetricResult(
                metric_id="residual_tolerance",
                value=None,
                confidence="failed",
                data_missing=True,
                missing_reason="No final residuals in simulation data",
            )

        max_residual = max(data.final_residuals.values())

        quality_checks = [{
            "name": "below_tolerance",
            "passed": max_residual < 1e-4,
            "message": f"Max residual: {max_residual:.2e} (target: <1e-4)",
        }]

        return MetricResult(
            metric_id="residual_tolerance",
            value=max_residual,
            unit="dimensionless",
            quality_checks=quality_checks,
            confidence="high" if max_residual < 1e-4 else "medium",
        )

    def _calc_max_courant(
        self,
        data: SimulationData,
        mdef: dict[str, Any] | None,
    ) -> MetricResult:
        """Get max Courant number."""
        if data.max_courant is None:
            return MetricResult(
                metric_id="max_courant",
                value=None,
                confidence="failed",
                data_missing=True,
                missing_reason="No Courant number data in simulation log",
            )

        quality_checks = [{
            "name": "below_threshold",
            "passed": data.max_courant < 1.0,
            "message": f"Max Courant: {data.max_courant:.4f} (target: <1.0)",
        }]

        return MetricResult(
            metric_id="max_courant",
            value=data.max_courant,
            unit="dimensionless",
            quality_checks=quality_checks,
            confidence="high" if data.max_courant < 1.0 else "medium",
        )


__all__ = ["MetricExecutor", "QualityCheckResult", "_time_averaged_stats"]
