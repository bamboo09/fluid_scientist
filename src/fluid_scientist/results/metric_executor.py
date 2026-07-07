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
        """Calculate pressure drop: p_inlet - p_outlet."""
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

        # Use last values (steady state)
        inlet_p = inlet_vals[-1]
        outlet_p = outlet_vals[-1]
        pressure_drop = inlet_p - outlet_p

        # Quality check: values should be stable
        if len(inlet_vals) > 5:
            recent = inlet_vals[-5:]
            mean_v = sum(recent) / len(recent)
            max_dev = max(abs(v - mean_v) for v in recent) / max(abs(mean_v), 1e-10)
            check = QualityCheckResult(
                "inlet_pressure_stability",
                max_dev < 0.05,
                f"Max deviation: {max_dev*100:.1f}%",
            )
            quality_checks.append(check.to_dict())
            if not check.passed:
                warnings.append(f"入口压力不稳定，偏差 {max_dev*100:.1f}%")

        return MetricResult(
            metric_id="pressure_drop",
            value=pressure_drop,
            unit="Pa",
            quality_checks=quality_checks,
            confidence="high" if all(q["passed"] for q in quality_checks) else "medium",
            warnings=warnings,
        )

    def _calc_drag_coefficient(
        self,
        data: SimulationData,
        mdef: dict[str, Any] | None,
    ) -> MetricResult:
        """Extract Cd from forceCoeffs data."""
        if "Cd" not in data.force_coefficients or not data.force_coefficients["Cd"]:
            return MetricResult(
                metric_id="drag_coefficient",
                value=None,
                confidence="failed",
                data_missing=True,
                missing_reason="No Cd data in forceCoeffs output",
            )

        cd_values = data.force_coefficients["Cd"]
        # Use time-averaged value for steady, or last value
        cd = cd_values[-1]

        quality_checks = []
        if len(cd_values) > 10:
            recent = cd_values[-10:]
            mean_cd = sum(recent) / len(recent)
            std_cd = math.sqrt(sum((v - mean_cd)**2 for v in recent) / len(recent))
            cv = std_cd / max(abs(mean_cd), 1e-10)
            check = QualityCheckResult(
                "cd_statistical_stability",
                cv < 0.1,
                f"CV of last 10 samples: {cv:.4f}",
            )
            quality_checks.append(check.to_dict())

        return MetricResult(
            metric_id="drag_coefficient",
            value=cd,
            unit="dimensionless",
            quality_checks=quality_checks,
            confidence="high" if all(q["passed"] for q in quality_checks) else "medium",
        )

    def _calc_lift_coefficient(
        self,
        data: SimulationData,
        mdef: dict[str, Any] | None,
    ) -> MetricResult:
        """Extract Cl from forceCoeffs data."""
        if "Cl" not in data.force_coefficients or not data.force_coefficients["Cl"]:
            return MetricResult(
                metric_id="lift_coefficient",
                value=None,
                confidence="failed",
                data_missing=True,
                missing_reason="No Cl data in forceCoeffs output",
            )

        cl_values = data.force_coefficients["Cl"]
        cl = cl_values[-1]

        return MetricResult(
            metric_id="lift_coefficient",
            value=cl,
            unit="dimensionless",
            confidence="high",
        )

    def _calc_strouhal_number(
        self,
        data: SimulationData,
        mdef: dict[str, Any] | None,
        parameters: dict[str, Any],
    ) -> MetricResult:
        """Calculate Strouhal number: St = f * D / U.

        Requires FFT of Cl or U probe data to find shedding frequency.
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

        # 2. Time step uniformity (assumed uniform from OpenFOAM)
        check2 = QualityCheckResult(
            "time_step_uniformity",
            True,
            "OpenFOAM output assumed uniformly sampled",
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

        # Perform FFT to find dominant frequency
        N = len(cl_values)
        # Remove mean
        mean_cl = sum(cl_values) / N
        detrended = [v - mean_cl for v in cl_values]

        # Simple DFT (for production, use numpy.fft)
        try:
            import numpy as np
            fft_result = np.fft.rfft(detrended)
            magnitudes = np.abs(fft_result)
            # Find peak (skip DC component)
            peak_idx = 1 + np.argmax(magnitudes[1:])
            # Need time step to get frequency
            # Estimate from parameters or time_sampling
            dt = parameters.get("time_step", parameters.get("interval", 0.01))
            frequency = peak_idx / (N * dt)

            # 4. Peak prominence
            if len(magnitudes) > peak_idx + 2:
                local_mean = np.mean(magnitudes[1:])
                prominence = magnitudes[peak_idx] / max(local_mean, 1e-10)
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
            # Fallback without numpy
            frequency = 0.0
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

        all_passed = all(q["passed"] for q in quality_checks)
        confidence = "high" if all_passed else ("medium" if sum(1 for q in quality_checks if q["passed"]) >= 4 else "low")

        return MetricResult(
            metric_id="strouhal_number",
            value=strouhal,
            unit="dimensionless",
            quality_checks=quality_checks,
            confidence=confidence,
            warnings=warnings,
        )

    def _calc_velocity_uniformity(
        self,
        data: SimulationData,
        mdef: dict[str, Any] | None,
    ) -> MetricResult:
        """Calculate velocity uniformity: CV = sigma_u / mean_u.

        CV = sqrt(mean(U^2) - mean(U)^2) / mean(U)

        Requires both mean(U) and mean(mag(U)) from surfaceFieldValue.
        """
        # Look for velocity mean and magnitude
        mean_u_key = None
        mag_u_key = None
        for key in data.surface_field_values:
            if "velocity" in key.lower() and "mean" in key.lower():
                mean_u_key = key
            if "velocity" in key.lower() and ("magnitude" in key.lower() or "mag" in key.lower()):
                mag_u_key = key

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

        mean_u = mean_u_vals[-1]

        if mag_u_key and mag_u_key in data.surface_field_values:
            mag_u = data.surface_field_values[mag_u_key][-1]
            # CV = sqrt(mean(U^2) - mean(U)^2) / mean(U)
            # mean(U^2) ≈ mag_u^2 (if mag(U) = |U|)
            # This is an approximation
            variance = max(mag_u ** 2 - mean_u ** 2, 0)
            std_u = math.sqrt(variance)
        else:
            # Use time variation as proxy
            if len(mean_u_vals) > 5:
                recent = mean_u_vals[-5:]
                mean_val = sum(recent) / len(recent)
                std_u = math.sqrt(sum((v - mean_val)**2 for v in recent) / len(recent))
            else:
                std_u = 0.0

        if abs(mean_u) < 1e-10:
            return MetricResult(
                metric_id="velocity_uniformity",
                value=None,
                confidence="failed",
                missing_reason="Mean velocity is zero, cannot calculate CV",
            )

        cv = std_u / abs(mean_u)

        quality_checks = [{
            "name": "has_magnitude_data",
            "passed": mag_u_key is not None,
            "message": "Magnitude data available for proper CV calculation" if mag_u_key else "Using time variation as proxy",
        }]

        return MetricResult(
            metric_id="velocity_uniformity",
            value=cv,
            unit="dimensionless",
            quality_checks=quality_checks,
            confidence="high" if mag_u_key else "medium",
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
        """Calculate friction factor: f = dp / (0.5 * rho * U^2 * L / D)."""
        # Get pressure drop from surface field values or metric
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

        dp = data.surface_field_values[inlet_key][-1] - data.surface_field_values[outlet_key][-1]

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

        return MetricResult(
            metric_id="friction_factor",
            value=f,
            unit="dimensionless",
            confidence="high",
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


__all__ = ["MetricExecutor", "QualityCheckResult"]
