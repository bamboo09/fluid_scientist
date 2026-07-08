"""Dynamic time sampling calculator.

Derives sampling parameters from physical characteristics instead of
using fixed defaults for all experiments.
"""

from __future__ import annotations

from dataclasses import dataclass

from fluid_scientist.measurement.models import TimeSamplingSpec


@dataclass
class PhysicalContext:
    """Physical context for time sampling derivation."""
    characteristic_length: float  # D for pipe/cylinder, H for cavity
    characteristic_velocity: float  # U_inlet, U_lid, etc.
    kinematic_viscosity: float | None = None
    estimated_frequency: float | None = None  # e.g., vortex shedding frequency
    is_transient: bool = True
    max_courant: float = 1.0
    user_end_time: float | None = None  # user-specified end time


class TimeSampler:
    """Calculate dynamic time sampling based on physical characteristics."""

    def calculate(self, ctx: PhysicalContext) -> TimeSamplingSpec:
        """Calculate time sampling from physical context.

        Rules:
        1. Convection time = L / U
        2. For transient with frequency:
           - sampling_interval <= 1 / (samples_per_cycle * estimated_frequency)
           - duration >= minimum_cycles / estimated_frequency
        3. For steady: shorter duration, coarser sampling
        4. Always respect max Courant number
        """
        if ctx.characteristic_velocity <= 0:
            return TimeSamplingSpec(
                start_time=0.0,
                end_time=100.0,
                interval=0.01,
                derivation_reason="Invalid velocity, using default",
            )

        # 1. Convection time
        convection_time = ctx.characteristic_length / ctx.characteristic_velocity

        # 2. Start time: typically 2-5 convection times to reach steady/transient state
        start_time = max(2.0 * convection_time, 1.0)

        # 3. End time and interval
        if ctx.is_transient and ctx.estimated_frequency is not None and ctx.estimated_frequency > 0:
            # Transient with known frequency (e.g., vortex shedding)
            samples_per_cycle = 20  # 20 samples per cycle for good spectral resolution
            minimum_cycles = 10  # need at least 10 cycles for spectral analysis

            sampling_interval = 1.0 / (samples_per_cycle * ctx.estimated_frequency)
            nyquist_freq = 1.0 / (2.0 * sampling_interval)

            duration = minimum_cycles / ctx.estimated_frequency
            end_time = start_time + duration

            # Respect max Courant
            if ctx.kinematic_viscosity and ctx.kinematic_viscosity > 0:
                dt_courant = (
                    ctx.max_courant
                    * (ctx.characteristic_length / 10) ** 2
                    / ctx.kinematic_viscosity
                )
                if dt_courant < sampling_interval:
                    sampling_interval = dt_courant

            reason = (
                f"瞬态采样: 特征长度={ctx.characteristic_length}m, "
                f"特征速度={ctx.characteristic_velocity}m/s, "
                f"对流时间={convection_time:.3f}s, "
                f"估计频率={ctx.estimated_frequency}Hz, "
                f"每周期{samples_per_cycle}点, 最少{minimum_cycles}周期, "
                f"Nyquist频率={nyquist_freq:.1f}Hz"
            )
        elif ctx.is_transient:
            # Transient without known frequency
            end_time = start_time + 10.0 * convection_time
            sampling_interval = convection_time / 20.0
            nyquist_freq = 1.0 / (2.0 * sampling_interval)

            reason = (
                f"瞬态采样(未知频率): 特征长度={ctx.characteristic_length}m, "
                f"特征速度={ctx.characteristic_velocity}m/s, "
                f"对流时间={convection_time:.3f}s, "
                f"采样间隔={sampling_interval:.4f}s (对流时间/20)"
            )
        else:
            # Steady state
            end_time = start_time + 5.0 * convection_time
            sampling_interval = convection_time / 10.0
            nyquist_freq = None

            reason = (
                f"稳态采样: 特征长度={ctx.characteristic_length}m, "
                f"特征速度={ctx.characteristic_velocity}m/s, "
                f"对流时间={convection_time:.3f}s, "
                f"采样5个对流时间"
            )

        # Override with user end time if provided
        if ctx.user_end_time is not None and ctx.user_end_time > end_time:
            end_time = ctx.user_end_time

        return TimeSamplingSpec(
            start_time=round(start_time, 4),
            end_time=round(end_time, 4),
            interval=round(sampling_interval, 6),
            write_control="runTime",
            characteristic_length=ctx.characteristic_length,
            characteristic_velocity=ctx.characteristic_velocity,
            convection_time=round(convection_time, 6),
            estimated_frequency=ctx.estimated_frequency,
            nyquist_frequency=round(nyquist_freq, 2) if nyquist_freq else None,
            samples_per_cycle=20 if ctx.is_transient and ctx.estimated_frequency else None,
            minimum_cycles=10 if ctx.is_transient and ctx.estimated_frequency else None,
            derivation_reason=reason,
        )


def estimate_vortex_shedding_frequency(
    diameter: float,
    velocity: float,
    reynolds: float | None = None,
) -> float | None:
    """Estimate vortex shedding frequency using Strouhal number.

    For cylinder flow: St ≈ 0.2 for 100 < Re < 200000
    f = St * U / D
    """
    if diameter <= 0 or velocity <= 0:
        return None
    # Default Strouhal number
    st = 0.2
    if reynolds is not None:
        if reynolds < 40:
            return None  # No shedding below Re=40
        elif reynolds < 200:
            st = 0.18 + (reynolds - 40) / 160 * 0.02  # linear interpolation
    return st * velocity / diameter


__all__ = ["PhysicalContext", "TimeSampler", "estimate_vortex_shedding_frequency"]
