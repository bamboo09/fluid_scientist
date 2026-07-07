"""Standard metric registry — pre-built MetricSpec per experiment type."""

from __future__ import annotations

from fluid_scientist.metric_spec.models import (
    MetricCategory,
    MetricDataType,
    MetricDefinition,
    MetricQualityCheck,
    MetricSpec,
    QualityCheckType,
)


def _cylinder_metrics() -> tuple[MetricDefinition, ...]:
    """Standard metrics for cylinder flow experiments."""
    return (
        MetricDefinition(
            metric_id="drag_coefficient",
            display_name="阻力系数 Cd",
            category=MetricCategory.PHYSICAL,
            data_type=MetricDataType.SCALAR,
            unit="dimensionless",
            formula="Fd / (0.5 * rho * U^2 * D)",
            function_object="forces",
            description="Drag coefficient on cylinder surface",
            critical=True,
        ),
        MetricDefinition(
            metric_id="lift_coefficient",
            display_name="升力系数 Cl",
            category=MetricCategory.PHYSICAL,
            data_type=MetricDataType.SCALAR,
            unit="dimensionless",
            formula="Fl / (0.5 * rho * U^2 * D)",
            function_object="forces",
            description="Lift coefficient on cylinder surface",
        ),
        MetricDefinition(
            metric_id="strouhal_number",
            display_name="Strouhal 数 St",
            category=MetricCategory.DIMENSIONLESS,
            data_type=MetricDataType.SCALAR,
            unit="dimensionless",
            formula="f * D / U",
            function_object="probes",
            description="Vortex shedding frequency dimensionless number",
        ),
        MetricDefinition(
            metric_id="pressure_drop",
            display_name="压降",
            category=MetricCategory.PHYSICAL,
            data_type=MetricDataType.SCALAR,
            unit="Pa",
            formula="p_inlet - p_outlet",
            function_object="surfaceFieldValue",
            description="Pressure difference between inlet and outlet",
        ),
        MetricDefinition(
            metric_id="residual_tolerance",
            display_name="残差容差",
            category=MetricCategory.CONVERGENCE,
            data_type=MetricDataType.SCALAR,
            unit="dimensionless",
            formula="max(initial_residuals)",
            description="Maximum residual for convergence assessment",
            critical=True,
        ),
    )


def _pipe_metrics() -> tuple[MetricDefinition, ...]:
    """Standard metrics for laminar pipe flow experiments."""
    return (
        MetricDefinition(
            metric_id="pressure_drop",
            display_name="压降",
            category=MetricCategory.PHYSICAL,
            data_type=MetricDataType.SCALAR,
            unit="Pa",
            formula="p_inlet - p_outlet",
            function_object="surfaceFieldValue",
            description="Pressure difference between inlet and outlet",
            critical=True,
        ),
        MetricDefinition(
            metric_id="friction_factor",
            display_name="摩擦系数 f",
            category=MetricCategory.DIMENSIONLESS,
            data_type=MetricDataType.SCALAR,
            unit="dimensionless",
            formula="dp / (0.5 * rho * U^2 * L / D)",
            description="Darcy friction factor",
        ),
        MetricDefinition(
            metric_id="reynolds_number",
            display_name="Reynolds 数",
            category=MetricCategory.DIMENSIONLESS,
            data_type=MetricDataType.SCALAR,
            unit="dimensionless",
            formula="rho * U * D / mu",
            description="Reynolds number for flow regime verification",
        ),
        MetricDefinition(
            metric_id="velocity_profile",
            display_name="速度剖面",
            category=MetricCategory.PHYSICAL,
            data_type=MetricDataType.VECTOR,
            unit="m/s",
            function_object="probes",
            description="Velocity profile at measurement cross-sections",
        ),
        MetricDefinition(
            metric_id="residual_tolerance",
            display_name="残差容差",
            category=MetricCategory.CONVERGENCE,
            data_type=MetricDataType.SCALAR,
            unit="dimensionless",
            formula="max(initial_residuals)",
            description="Maximum residual for convergence assessment",
            critical=True,
        ),
    )


def _cavity_metrics() -> tuple[MetricDefinition, ...]:
    """Standard metrics for lid-driven cavity experiments."""
    return (
        MetricDefinition(
            metric_id="velocity_profile",
            display_name="速度剖面",
            category=MetricCategory.PHYSICAL,
            data_type=MetricDataType.VECTOR,
            unit="m/s",
            function_object="probes",
            description="Velocity profile along centerlines",
            critical=True,
        ),
        MetricDefinition(
            metric_id="pressure_profile",
            display_name="压力剖面",
            category=MetricCategory.PHYSICAL,
            data_type=MetricDataType.VECTOR,
            unit="Pa",
            function_object="probes",
            description="Pressure profile along centerlines",
        ),
        MetricDefinition(
            metric_id="vortex_center_x",
            display_name="涡心 X 坐标",
            category=MetricCategory.PHYSICAL,
            data_type=MetricDataType.SCALAR,
            unit="m",
            formula="argmin(|velocity|) along x",
            description="X coordinate of primary vortex center",
        ),
        MetricDefinition(
            metric_id="vortex_center_y",
            display_name="涡心 Y 坐标",
            category=MetricCategory.PHYSICAL,
            data_type=MetricDataType.SCALAR,
            unit="m",
            formula="argmin(|velocity|) along y",
            description="Y coordinate of primary vortex center",
        ),
        MetricDefinition(
            metric_id="residual_tolerance",
            display_name="残差容差",
            category=MetricCategory.CONVERGENCE,
            data_type=MetricDataType.SCALAR,
            unit="dimensionless",
            formula="max(initial_residuals)",
            description="Maximum residual for convergence assessment",
            critical=True,
        ),
    )


def _standard_quality_checks() -> tuple[MetricQualityCheck, ...]:
    """Standard quality checks applicable to all experiment types."""
    return (
        MetricQualityCheck(
            check_id="residual_convergence",
            check_type=QualityCheckType.RESIDUAL_TOLERANCE,
            threshold=1e-4,
            metric_id="residual_tolerance",
            description="Residuals must be below 1e-4 for convergence",
        ),
        MetricQualityCheck(
            check_id="mass_conservation",
            check_type=QualityCheckType.MASS_IMBALANCE,
            threshold=1.0,
            description="Mass imbalance must be below 1%",
        ),
    )


_METRIC_BUILDERS = {
    "laminar_pipe": _pipe_metrics,
    "cylinder_flow": _cylinder_metrics,
    "lid_driven_cavity": _cavity_metrics,
}

_REGISTRY: dict[str, MetricSpec] = {}


def get_metric_spec(experiment_type: str) -> MetricSpec:
    """Get the standard MetricSpec for an experiment type.

    Raises:
        KeyError: if no standard metrics are registered for the type.
    """
    if experiment_type not in _REGISTRY:
        builder = _METRIC_BUILDERS.get(experiment_type)
        if builder is None:
            raise KeyError(
                f"no standard metrics registered for '{experiment_type}'"
            )
        _REGISTRY[experiment_type] = MetricSpec(
            spec_id=f"standard-{experiment_type}",
            experiment_type=experiment_type,
            metrics=builder(),
            quality_checks=_standard_quality_checks(),
        )
    return _REGISTRY[experiment_type]


def registered_types() -> tuple[str, ...]:
    """Return all experiment types with registered metric specs."""
    return tuple(sorted(_METRIC_BUILDERS.keys()))


__all__ = [
    "get_metric_spec",
    "registered_types",
]
