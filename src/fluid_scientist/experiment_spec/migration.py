"""Migration: convert existing ExperimentPlan variants to ExperimentSpec.

Each existing plan type (laminar_pipe, cylinder_flow, lid_driven_cavity)
has its own case dataclass.  This module converts them into the unified
ExperimentSpec format with structured ParameterSpec entries.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fluid_scientist.compat import UTC
from fluid_scientist.experiment_spec.models import (
    CodeBinding,
    Compressibility,
    ConfirmationPolicy,
    Criticality,
    Dimensions,
    ExperimentSpec,
    ExperimentStatus,
    FlowRegime,
    InteractionMode,
    ParameterConstraints,
    ParameterDependency,
    ParameterProvenance,
    ParameterSource,
    ParameterSourceInfo,
    ParameterSpec,
    ParameterStatus,
    PhaseType,
    PhysicsSpec,
    ResearchSpec,
    TaskType,
    TemporalType,
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _param(
    pid: str,
    display: str,
    category: str,
    value: float | int | str | bool | None,
    *,
    unit: str | None = None,
    data_type: str = "float",
    source_type: str = "template_default",
    criticality: str = "medium",
    confirmation: str = "recommend_and_notify",
    visible: str = "standard",
    depends_on: list[str] | None = None,
    affects: list[str] | None = None,
    min_val: float | None = None,
    max_val: float | None = None,
    exclusive_min: bool = False,
    file: str | None = None,
    path: str | None = None,
    serializer: str = "scalar",
    reason: str | None = None,
    confidence: str = "medium",
    applicability: str | None = None,
    risk_level: str = "low",
    status: str = "accepted",
) -> ParameterSpec:
    return ParameterSpec(
        parameter_id=pid,
        display_name=display,
        category=category,
        value=value,
        unit=unit,
        data_type=data_type,
        source=ParameterSourceInfo(
            type=ParameterSource(source_type),
            reason=reason,
            confidence=confidence,
            applicability=applicability,
            risk_level=risk_level,
        ),
        status=ParameterStatus(status),
        editable=True,
        visible_level=InteractionMode(visible),
        criticality=Criticality(criticality),
        impact_scope=affects or [],
        confirmation_policy=ConfirmationPolicy(confirmation),
        constraints=ParameterConstraints(
            min=min_val, max=max_val, exclusive_min=exclusive_min
        ),
        dependencies=ParameterDependency(
            depends_on=depends_on or [], affects=affects or []
        ),
        provenance=ParameterProvenance(created_by="system", created_at=_now()),
        code_binding=CodeBinding(
            target_file=file or "", target_path=path or "", serializer=serializer
        ) if file else None,
    )


def migrate_cylinder_plan(
    plan: Any,
    experiment_id: str,
    project_id: str | None = None,
) -> ExperimentSpec:
    """Convert a CylinderFlowExperimentPlan to ExperimentSpec."""
    case = plan.case
    physics = PhysicsSpec(
        dimensions=Dimensions.TWO_D,
        phases=PhaseType.SINGLE_PHASE,
        compressibility=Compressibility.INCOMPRESSIBLE,
        flow_regime=FlowRegime.LAMINAR if case.reynolds_number < 2300 else FlowRegime.TURBULENT,
        temporal_type=TemporalType.TRANSIENT,
        gravity_enabled=False,
    )
    research = ResearchSpec(
        title=plan.experiment_name,
        objective=plan.objective,
        hypothesis=None,
        comparison_target=None,
        user_questions=list(plan.requested_outputs),
    )
    params = [
        _param("reynolds_number", "Reynolds数", "physics", case.reynolds_number,
               data_type="float", source_type="user", criticality="critical",
               confirmation="require_explicit",
               affects=["inlet_velocity", "flow_regime"],
               reason="用户提供的目标雷诺数",
               confidence="high", status="pending",
               file="constant/transportProperties", path="nu"),
        _param("diameter", "圆柱直径", "geometry", case.diameter_m,
               unit="m", source_type="user", criticality="critical",
               confirmation="require_explicit",
               affects=["reynolds_number", "domain_width", "mesh_resolution",
                        "force_coefficient"],
               reason="用户指定的圆柱直径",
               confidence="high", status="pending",
               file="constant/dynamicMeshDict", path="D"),
        _param("inlet_velocity", "入口速度", "boundary_condition",
               case.inlet_velocity_m_s if hasattr(case, 'inlet_velocity_m_s') else None,
               unit="m/s", source_type="derived", criticality="high",
               depends_on=["reynolds_number", "diameter", "kinematic_viscosity"],
               affects=["dynamic_pressure", "time_step", "courant_number"],
               file="0/U", path="boundaryField.inlet.value"),
        _param("kinematic_viscosity", "运动粘度", "material",
               case.kinematic_viscosity_m2_s if hasattr(case, 'kinematic_viscosity_m2_s') else None,
               unit="m^2/s", source_type="template_default", criticality="high",
               depends_on=["reynolds_number"],
               reason="来自实验计划", confidence="medium",
               file="constant/transportProperties", path="nu"),
        _param("density", "密度", "material",
               case.density_kg_m3 if hasattr(case, 'density_kg_m3') else None,
               unit="kg/m^3", source_type="template_default", criticality="high",
               affects=["dynamic_pressure", "force_coefficient"],
               reason="来自实验计划", confidence="medium",
               file="constant/transportProperties", path="rho"),
        _param("domain_width", "计算域宽度", "geometry",
               case.domain_width_d if hasattr(case, 'domain_width_d') else None,
               unit="D", criticality="medium", confirmation="recommend_and_notify",
               depends_on=["diameter"],
               file="system/blockMeshDict", path="width"),
        _param("domain_height", "计算域高度", "geometry",
               case.domain_height_d if hasattr(case, 'domain_height_d') else None,
               unit="D", criticality="medium", confirmation="recommend_and_notify",
               depends_on=["diameter"],
               file="system/blockMeshDict", path="height"),
        _param("cells_radial", "径向网格数", "mesh",
               case.cells_radial if hasattr(case, 'cells_radial') else None,
               data_type="integer", criticality="medium",
               affects=["mesh_resolution"],
               file="system/blockMeshDict", path="cellsRadial"),
        _param("cells_wake", "尾流网格数", "mesh",
               case.cells_wake if hasattr(case, 'cells_wake') else None,
               data_type="integer", criticality="medium",
               affects=["mesh_resolution"],
               file="system/blockMeshDict", path="cellsWake"),
        _param("end_time", "结束时间", "numerics",
               case.end_time_s,
               unit="s", criticality="high",
               affects=["sampling_plan"],
               file="system/controlDict", path="endTime"),
        _param("time_step", "时间步长", "numerics",
               case.time_step_s if hasattr(case, 'time_step_s') and case.time_step_s else None,
               unit="s", source_type="derived", criticality="high",
               depends_on=["max_courant", "inlet_velocity", "cell_size"],
               affects=["courant_number", "sampling_frequency"],
               file="system/controlDict", path="deltaT"),
        _param("max_courant", "最大Courant数", "numerics",
               case.max_courant if hasattr(case, 'max_courant') and case.max_courant else 0.5,
               data_type="float", criticality="high", confirmation="recommend_and_notify",
               affects=["time_step"],
               file="system/controlDict", path="maxCo"),
    ]
    return ExperimentSpec(
        experiment_id=experiment_id,
        schema_version="1.0.0",
        experiment_version=1,
        status=ExperimentStatus.DRAFT,
        task_type=TaskType.MECHANISM_ANALYSIS,
        interaction_mode=InteractionMode.STANDARD,
        research=research,
        physics=physics,
        parameters=params,
        created_at=_now(),
        updated_at=_now(),
    )


def migrate_pipe_plan(
    plan: Any,
    experiment_id: str,
    project_id: str | None = None,
) -> ExperimentSpec:
    """Convert a PipeExperimentPlan to ExperimentSpec."""
    case = plan.case
    re = case.reynolds_number
    physics = PhysicsSpec(
        dimensions=Dimensions.AXISYMMETRIC,
        phases=PhaseType.SINGLE_PHASE,
        compressibility=Compressibility.INCOMPRESSIBLE,
        flow_regime=FlowRegime.LAMINAR if re < 2300 else FlowRegime.TURBULENT,
        temporal_type=TemporalType.STEADY,
        gravity_enabled=False,
    )
    research = ResearchSpec(
        title=plan.experiment_name,
        objective=plan.objective,
    )
    params = [
        _param("diameter", "管径", "geometry", case.diameter_m,
               unit="m", source_type="user", criticality="critical",
               affects=["reynolds_number", "hydraulic_diameter", "mesh_resolution"],
               reason="用户指定的管径",
               confidence="high", status="pending"),
        _param("length", "管长", "geometry", case.length_m,
               unit="m", source_type="user", criticality="high",
               reason="用户指定的管长",
               confidence="high", status="pending",
               file="system/blockMeshDict", path="length"),
        _param("mean_velocity", "平均速度",
               "boundary_condition", case.mean_velocity_m_s,
               unit="m/s", source_type="derived", criticality="critical",
               depends_on=["reynolds_number", "diameter", "kinematic_viscosity"],
               file="0/U", path="boundaryField.inlet.value"),
        _param("kinematic_viscosity", "运动粘度", "material",
               case.kinematic_viscosity_m2_s,
               unit="m^2/s", criticality="high",
               file="constant/transportProperties", path="nu"),
        _param("density", "密度", "material", case.density_kg_m3,
               unit="kg/m^3", criticality="high",
               file="constant/transportProperties", path="rho"),
        _param("reynolds_number", "Reynolds数", "physics", re,
               data_type="float", source_type="user", criticality="critical",
               depends_on=["diameter", "mean_velocity", "kinematic_viscosity"],
               affects=["flow_regime"],
               reason="用户指定的目标雷诺数",
               confidence="high", status="pending"),
        _param("axial_cells", "轴向网格数", "mesh",
               case.axial_cells,
               data_type="integer", criticality="medium",
               file="system/blockMeshDict", path="axialCells"),
        _param("radial_cells", "径向网格数", "mesh",
               case.radial_cells,
               data_type="integer", criticality="medium",
               file="system/blockMeshDict", path="radialCells"),
    ]
    return ExperimentSpec(
        experiment_id=experiment_id,
        schema_version="1.0.0",
        experiment_version=1,
        status=ExperimentStatus.DRAFT,
        task_type=TaskType.NEW_SIMULATION,
        interaction_mode=InteractionMode.STANDARD,
        research=research,
        physics=physics,
        parameters=params,
        created_at=_now(),
        updated_at=_now(),
    )


def migrate_cavity_plan(
    plan: Any,
    experiment_id: str,
    project_id: str | None = None,
) -> ExperimentSpec:
    """Convert a CavityExperimentPlan to ExperimentSpec."""
    case = plan.case
    physics = PhysicsSpec(
        dimensions=Dimensions.TWO_D,
        phases=PhaseType.SINGLE_PHASE,
        compressibility=Compressibility.INCOMPRESSIBLE,
        flow_regime=FlowRegime.LAMINAR,
        temporal_type=TemporalType.TRANSIENT,
        gravity_enabled=False,
    )
    research = ResearchSpec(
        title=plan.experiment_name,
        objective=plan.objective,
    )
    params = [
        _param("side_length", "边长", "geometry", case.side_length_m,
               unit="m", source_type="user", criticality="critical",
               affects=["mesh_resolution"],
               reason="用户指定的方腔边长",
               confidence="high", status="pending"),
        _param("lid_velocity", "顶盖速度",
               "boundary_condition", case.lid_velocity_m_s,
               unit="m/s", source_type="user", criticality="critical",
               reason="用户指定的顶盖速度",
               confidence="high", status="pending",
               file="0/U", path="boundaryField.lid.value"),
        _param("kinematic_viscosity", "运动粘度", "material",
               case.kinematic_viscosity_m2_s,
               unit="m^2/s", criticality="high",
               file="constant/transportProperties", path="nu"),
        _param("density", "密度", "material", case.density_kg_m3,
               unit="kg/m^3", criticality="high",
               file="constant/transportProperties", path="rho"),
        _param("cells_per_side", "每边网格数", "mesh",
               case.cells_per_side,
               data_type="integer", criticality="medium",
               file="system/blockMeshDict", path="cells"),
        _param("end_time", "结束时间", "numerics",
               case.end_time_s,
               unit="s", criticality="high",
               file="system/controlDict", path="endTime"),
    ]
    return ExperimentSpec(
        experiment_id=experiment_id,
        schema_version="1.0.0",
        experiment_version=1,
        status=ExperimentStatus.DRAFT,
        task_type=TaskType.BENCHMARK_REPRODUCTION,
        interaction_mode=InteractionMode.STANDARD,
        research=research,
        physics=physics,
        parameters=params,
        created_at=_now(),
        updated_at=_now(),
    )


def _criticality_str(p: ParameterSpec) -> str:
    """Extract criticality value safely from a parameter."""
    return p.criticality.value if hasattr(p.criticality, 'value') else str(p.criticality)


def apply_recommendations(spec: ExperimentSpec) -> ExperimentSpec:
    """Apply system recommendations to fill in missing or improvable parameters.

    This ensures new experiment drafts come pre-filled with sensible defaults
    rather than requiring users to fill every parameter from scratch.
    """
    params = list(spec.parameters)
    param_map = {p.parameter_id: p for p in params}
    changed = False

    # Water properties (most common fluid)
    if "density" in param_map:
        p = param_map["density"]
        if p.value is None or p.source.type == ParameterSource.UNKNOWN:
            param_map["density"] = _param(
                "density", p.display_name, p.category, 998.2,
                unit="kg/m^3", source_type="system_recommended",
                criticality=_criticality_str(p),
                confirmation="recommend_and_notify",
                reason="假设流体为室温水（20°C）",
                confidence="high",
                applicability="适用于水流仿真；若流体非水请修改",
                risk_level="medium",
                status="pending",
                affects=p.impact_scope,
                file=p.code_binding.target_file if p.code_binding else None,
                path=p.code_binding.target_path if p.code_binding else None,
            )
            changed = True

    if "kinematic_viscosity" in param_map:
        p = param_map["kinematic_viscosity"]
        if p.value is None or p.source.type == ParameterSource.UNKNOWN:
            param_map["kinematic_viscosity"] = _param(
                "kinematic_viscosity", p.display_name, p.category, 1.0e-6,
                unit="m^2/s", source_type="system_recommended",
                criticality=_criticality_str(p),
                confirmation="recommend_and_notify",
                reason="假设流体为室温水（20°C）"
                        "，ν = 1.0×10⁻⁶ m²/s",
                confidence="high",
                applicability="适用于水流仿真；若流体非水请修改",
                risk_level="medium",
                status="pending",
                affects=p.impact_scope,
                file=p.code_binding.target_file if p.code_binding else None,
                path=p.code_binding.target_path if p.code_binding else None,
            )
            changed = True

    # Cylinder domain recommendations
    if "domain_width" in param_map:
        p = param_map["domain_width"]
        if p.value is None or p.source.type == ParameterSource.UNKNOWN:
            param_map["domain_width"] = _param(
                "domain_width", p.display_name, p.category, 10.0,
                unit="D", source_type="system_recommended",
                criticality="medium", confirmation="recommend_and_notify",
                depends_on=["diameter"],
                reason="上游10D为圆柱绕流常用域尺寸，保证入口均匀",
                confidence="high",
                applicability="适用于外流绕流问题",
                risk_level="low",
                status="pending",
                affects=p.impact_scope,
                file=p.code_binding.target_file if p.code_binding else None,
                path=p.code_binding.target_path if p.code_binding else None,
            )
            changed = True

    if "domain_height" in param_map:
        p = param_map["domain_height"]
        if p.value is None or p.source.type == ParameterSource.UNKNOWN:
            param_map["domain_height"] = _param(
                "domain_height", p.display_name, p.category, 20.0,
                unit="D", source_type="system_recommended",
                criticality="medium", confirmation="recommend_and_notify",
                depends_on=["diameter"],
                reason="下游20D保证涶脱落充分发展",
                confidence="high",
                applicability="适用于外流绕流问题",
                risk_level="low",
                status="pending",
                affects=p.impact_scope,
                file=p.code_binding.target_file if p.code_binding else None,
                path=p.code_binding.target_path if p.code_binding else None,
            )
            changed = True

    # Max Courant for transient
    if "max_courant" in param_map:
        p = param_map["max_courant"]
        if p.value is None or p.source.type == ParameterSource.UNKNOWN:
            param_map["max_courant"] = _param(
                "max_courant", p.display_name, p.category, 0.5,
                data_type="float", source_type="system_recommended",
                criticality="high", confirmation="recommend_and_notify",
                reason="瞬态计算推荐 Co ≤ 0.5 保证稳定性",
                confidence="high",
                applicability="适用于瞬态求解",
                risk_level="medium",
                status="pending",
                affects=p.impact_scope,
                file=p.code_binding.target_file if p.code_binding else None,
                path=p.code_binding.target_path if p.code_binding else None,
            )
            changed = True

    if not changed:
        return spec

    new_params = [param_map.get(p.parameter_id, p) for p in params]
    return spec.model_copy(update={"parameters": new_params, "updated_at": _now()})


def migrate_plan(plan: Any, experiment_id: str, project_id: str | None = None) -> ExperimentSpec:
    """Auto-detect plan type and migrate to ExperimentSpec."""
    et = getattr(plan, "experiment_type", None)
    if et == "cylinder_flow":
        spec = migrate_cylinder_plan(plan, experiment_id, project_id)
    elif et == "laminar_pipe":
        spec = migrate_pipe_plan(plan, experiment_id, project_id)
    elif et == "lid_driven_cavity":
        spec = migrate_cavity_plan(plan, experiment_id, project_id)
    else:
        raise ValueError(f"cannot migrate plan type '{et}' to ExperimentSpec")
    return apply_recommendations(spec)
