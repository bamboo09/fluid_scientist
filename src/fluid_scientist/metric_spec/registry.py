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
            required_data=[
                "forceCoeffs time series",
                "cylinder_diameter",
                "inlet_velocity",
                "fluid_density",
            ],
            quality_checks=[
                "sampling_frequency",
                "minimum_cycles",
                "statistical_convergence",
            ],
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
            required_data=[
                "forceCoeffs time series",
                "cylinder_diameter",
                "inlet_velocity",
                "fluid_density",
            ],
            quality_checks=[
                "sampling_frequency",
                "minimum_cycles",
                "statistical_convergence",
            ],
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
            required_data=[
                "lift_coefficient time series",
                "cylinder_diameter",
                "inlet_velocity",
            ],
            quality_checks=[
                "sampling_frequency",
                "minimum_cycles",
                "peak_prominence",
            ],
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
            required_data=[
                "inlet/outlet surfaceFieldValue",
                "inlet_boundary_pressure",
                "outlet_boundary_pressure",
            ],
            quality_checks=[
                "mass_balance",
                "statistical_convergence",
            ],
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
            required_data=[
                "solver residual log",
            ],
            quality_checks=[
                "residual_tolerance_threshold",
            ],
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
            required_data=[
                "inlet/outlet surfaceFieldValue",
                "inlet_boundary_pressure",
                "outlet_boundary_pressure",
            ],
            quality_checks=[
                "mass_balance",
                "statistical_convergence",
            ],
        ),
        MetricDefinition(
            metric_id="friction_factor",
            display_name="摩擦系数 f",
            category=MetricCategory.DIMENSIONLESS,
            data_type=MetricDataType.SCALAR,
            unit="dimensionless",
            formula="dp / (0.5 * rho * U^2 * L / D)",
            description="Darcy friction factor",
            required_data=[
                "pressure_drop",
                "pipe_diameter",
                "pipe_length",
                "mean_velocity",
                "fluid_density",
            ],
            quality_checks=[
                "reynolds_number_range",
                "statistical_convergence",
            ],
        ),
        MetricDefinition(
            metric_id="reynolds_number",
            display_name="Reynolds 数",
            category=MetricCategory.DIMENSIONLESS,
            data_type=MetricDataType.SCALAR,
            unit="dimensionless",
            formula="rho * U * D / mu",
            description="Reynolds number for flow regime verification",
            required_data=[
                "pipe_diameter",
                "mean_velocity",
                "fluid_density",
                "fluid_viscosity",
            ],
            quality_checks=[
                "flow_regime_consistency",
            ],
        ),
        MetricDefinition(
            metric_id="velocity_profile",
            display_name="速度剖面",
            category=MetricCategory.PHYSICAL,
            data_type=MetricDataType.VECTOR,
            unit="m/s",
            function_object="probes",
            description="Velocity profile at measurement cross-sections",
            required_data=[
                "probes along pipe cross-sections",
                "axial_velocity_field",
            ],
            quality_checks=[
                "statistical_convergence",
                "symmetry_check",
            ],
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
            required_data=[
                "solver residual log",
            ],
            quality_checks=[
                "residual_tolerance_threshold",
            ],
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
            required_data=[
                "probes along cavity centerlines",
                "velocity_field",
            ],
            quality_checks=[
                "statistical_convergence",
                "symmetry_check",
            ],
        ),
        MetricDefinition(
            metric_id="pressure_profile",
            display_name="压力剖面",
            category=MetricCategory.PHYSICAL,
            data_type=MetricDataType.VECTOR,
            unit="Pa",
            function_object="probes",
            description="Pressure profile along centerlines",
            required_data=[
                "probes along cavity centerlines",
                "pressure_field",
            ],
            quality_checks=[
                "statistical_convergence",
            ],
        ),
        MetricDefinition(
            metric_id="vortex_center_x",
            display_name="涡心 X 坐标",
            category=MetricCategory.PHYSICAL,
            data_type=MetricDataType.SCALAR,
            unit="m",
            formula="argmin(|velocity|) along x",
            description="X coordinate of primary vortex center",
            required_data=[
                "velocity_field",
                "cavity_geometry",
            ],
            quality_checks=[
                "statistical_convergence",
            ],
        ),
        MetricDefinition(
            metric_id="vortex_center_y",
            display_name="涡心 Y 坐标",
            category=MetricCategory.PHYSICAL,
            data_type=MetricDataType.SCALAR,
            unit="m",
            formula="argmin(|velocity|) along y",
            description="Y coordinate of primary vortex center",
            required_data=[
                "velocity_field",
                "cavity_geometry",
            ],
            quality_checks=[
                "statistical_convergence",
            ],
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
            required_data=[
                "solver residual log",
            ],
            quality_checks=[
                "residual_tolerance_threshold",
            ],
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
