"""MetricPlanner — generate metric plans and measurement plans from research goals.

The ``MetricPlanner`` bridges the research intent (what the user wants to know)
and the simulation configuration (what to sample inside a single run).  It
produces a :class:`MetricPlan` that categorises requested metrics into
core / credibility / extension buckets and a :class:`MeasurementPlan` that
materialises the corresponding OpenFOAM functionObjects, spatial sampling
locations, field outputs and time-sampling strategy.

Categorisation rules (adapted to the existing ``MetricCategory`` enum):

* **core** — metrics explicitly requested by the user or flagged ``critical``
  in the registry (e.g. ``drag_coefficient`` for cylinder flow).
* **credibility** — convergence/numerical-quality metrics
  (``MetricCategory.CONVERGENCE``), e.g. ``residual_tolerance``.
* **extension** — remaining registry metrics that are relevant but not
  explicitly requested.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from fluid_scientist.measurement.models import (
    FieldOutputSpec,
    FunctionObjectSpec,
    FunctionObjectType,
    MeasurementPlan,
    MetricBinding,
    SpatialSamplingSpec,
    SpatialSamplingType,
    TimeSamplingSpec,
)
from fluid_scientist.metric_spec.models import MetricCategory
from fluid_scientist.metric_spec.registry import get_metric_spec

if TYPE_CHECKING:
    from fluid_scientist.research.models import ResearchPhysicsSpec


class MetricPlan(BaseModel):
    """指标计划。"""

    core_metrics: list[str] = Field(default_factory=list)
    credibility_metrics: list[str] = Field(default_factory=list)
    extension_metrics: list[str] = Field(default_factory=list)
    unknown_metrics: list[str] = Field(default_factory=list)
    measurement_plan: MeasurementPlan


class MetricPlanner:
    """从研究目标和物理规格生成指标计划。"""

    def propose_metrics(
        self,
        research_objective: str,
        physics_spec: ResearchPhysicsSpec | None = None,
        user_metrics: list[str] | None = None,
        experiment_type: str = "unknown",
    ) -> MetricPlan:
        """提议指标并生成测量计划。

        Args:
            research_objective: 研究目标描述（用于将来扩展，当前保留）。
            physics_spec: 研究物理规格，可选。
            user_metrics: 用户显式请求的指标 ID 列表。
            experiment_type: 实验类型，用于匹配 registry 中的标准指标。

        Returns:
            包含指标分类和测量计划的 :class:`MetricPlan`。
        """
        user_metrics = user_metrics or []
        core_metrics: list[str] = []
        credibility_metrics: list[str] = []
        extension_metrics: list[str] = []
        unknown_metrics: list[str] = []

        user_set = set(user_metrics)

        # 1. 从 registry 获取标准指标
        #    CONVERGENCE 类指标（如 residual_tolerance）优先归入 credibility，
        #    即使被标记为 critical 也不应进入 core。
        try:
            registry_spec = get_metric_spec(experiment_type)
            for metric_def in registry_spec.metrics:
                if metric_def.category == MetricCategory.CONVERGENCE:
                    credibility_metrics.append(metric_def.metric_id)
                elif metric_def.metric_id in user_set or metric_def.critical:
                    core_metrics.append(metric_def.metric_id)
                else:
                    extension_metrics.append(metric_def.metric_id)
        except Exception:
            # 无匹配的 registry spec — 仅依赖用户指标
            pass

        # 2. 补充分类：用户请求的指标若尚未被 registry 分类，则按是否为
        #    标准指标归入 core（已知标准指标）或 unknown（完全未知）。
        known_metrics = set(core_metrics + credibility_metrics + extension_metrics)
        for m in user_metrics:
            if m in known_metrics:
                continue
            if self._is_standard_metric(m):
                core_metrics.append(m)
            else:
                unknown_metrics.append(m)

        # 3. 生成 MeasurementPlan（用户指标 + 核心 registry 指标）
        plan_metrics = list(dict.fromkeys(core_metrics + user_metrics))
        measurement_plan = self._generate_measurement_plan(plan_metrics, experiment_type)

        return MetricPlan(
            core_metrics=core_metrics,
            credibility_metrics=credibility_metrics,
            extension_metrics=extension_metrics,
            unknown_metrics=unknown_metrics,
            measurement_plan=measurement_plan,
        )

    @staticmethod
    def _is_standard_metric(metric_id: str) -> bool:
        """检查指标是否是已知的标准指标（即使不在当前 registry 中）。"""
        standard = {
            "drag_coefficient",
            "lift_coefficient",
            "strouhal_number",
            "pressure_drop",
            "friction_factor",
            "reynolds_number",
            "velocity_profile",
            "pressure_coefficient",
            "vortex_center",
            "mass_flow_rate",
            "outlet_velocity_uniformity",
            "pressure_profile",
            "vortex_center_x",
            "vortex_center_y",
            "residual_tolerance",
        }
        return metric_id in standard

    @staticmethod
    def _generate_measurement_plan(
        metrics: list[str],
        experiment_type: str,
    ) -> MeasurementPlan:
        """根据指标列表生成测量计划。"""
        metric_set = set(metrics)

        required_fields = [
            FieldOutputSpec(field_name="U", write_interval=100),
            FieldOutputSpec(field_name="p", write_interval=100),
        ]
        function_objects: list[FunctionObjectSpec] = []
        spatial_sampling: list[SpatialSamplingSpec] = []
        metric_bindings: list[MetricBinding] = []

        # 压降 → 入口/出口截面压力采样
        if "pressure_drop" in metric_set:
            inlet_id = "inlet_section"
            outlet_id = "outlet_section"
            spatial_sampling.extend([
                SpatialSamplingSpec(
                    id=inlet_id,
                    type=SpatialSamplingType.SURFACE,
                    description="入口截面",
                ),
                SpatialSamplingSpec(
                    id=outlet_id,
                    type=SpatialSamplingType.SURFACE,
                    description="出口截面",
                ),
            ])
            for surface_id in (inlet_id, outlet_id):
                function_objects.append(FunctionObjectSpec(
                    type=FunctionObjectType.SURFACE_FIELD_VALUE,
                    name=f"pressure_{surface_id}",
                    field="p",
                    operation="areaAverage",
                    surface=surface_id,
                ))
            metric_bindings.append(MetricBinding(
                metric_id="pressure_drop",
                source=outlet_id,
                function_object=f"pressure_{outlet_id}",
            ))

        # 阻力/升力系数 → forceCoeffs
        if "drag_coefficient" in metric_set or "lift_coefficient" in metric_set:
            patch_name = "cylinder" if experiment_type == "cylinder_flow" else "wall"
            function_objects.append(FunctionObjectSpec(
                type=FunctionObjectType.FORCE_COEFFS,
                name="forceCoeffs_1",
                target_patch=patch_name,
            ))
            if "drag_coefficient" in metric_set:
                metric_bindings.append(MetricBinding(
                    metric_id="drag_coefficient",
                    source="forceCoeffs_1",
                    function_object="forceCoeffs_1",
                ))
            if "lift_coefficient" in metric_set:
                metric_bindings.append(MetricBinding(
                    metric_id="lift_coefficient",
                    source="forceCoeffs_1",
                    function_object="forceCoeffs_1",
                ))

        # Strouhal 数 → forceCoeffs + 时间序列
        if "strouhal_number" in metric_set:
            patch_name = "cylinder" if experiment_type == "cylinder_flow" else "wall"
            if not any(fo.type == FunctionObjectType.FORCE_COEFFS for fo in function_objects):
                function_objects.append(FunctionObjectSpec(
                    type=FunctionObjectType.FORCE_COEFFS,
                    name="forceCoeffs_1",
                    target_patch=patch_name,
                ))
            metric_bindings.append(MetricBinding(
                metric_id="strouhal_number",
                source="forceCoeffs_1",
                function_object="forceCoeffs_1",
            ))

        # 出口速度均匀性 → 出口截面速度采样
        if "outlet_velocity_uniformity" in metric_set:
            outlet_id = "outlet_uniformity_section"
            spatial_sampling.append(SpatialSamplingSpec(
                id=outlet_id,
                type=SpatialSamplingType.SURFACE,
                description="出口速度均匀性截面",
            ))
            function_objects.append(FunctionObjectSpec(
                type=FunctionObjectType.SURFACE_FIELD_VALUE,
                name="velocity_outlet",
                field="U",
                operation="areaAverage",
                surface=outlet_id,
            ))
            metric_bindings.append(MetricBinding(
                metric_id="outlet_velocity_uniformity",
                source=outlet_id,
                function_object="velocity_outlet",
            ))

        # 速度剖面 → 线采样
        if "velocity_profile" in metric_set:
            line_id = "velocity_line"
            spatial_sampling.append(SpatialSamplingSpec(
                id=line_id,
                type=SpatialSamplingType.LINE,
                description="速度剖面采样线",
            ))
            metric_bindings.append(MetricBinding(
                metric_id="velocity_profile",
                source=line_id,
            ))

        # 时间采样配置
        time_sampling = TimeSamplingSpec(
            start_time=20.0,
            end_time=100.0,
            interval=0.01,
        )
        # 瞬态指标（如 Strouhal）需要更密集的时间采样
        if "strouhal_number" in metric_set:
            time_sampling = TimeSamplingSpec(
                start_time=20.0,
                end_time=200.0,
                interval=0.005,
            )

        return MeasurementPlan(
            required_fields=required_fields,
            function_objects=function_objects,
            spatial_sampling=spatial_sampling,
            time_sampling=time_sampling,
            metric_bindings=metric_bindings,
        )


__all__ = ["MetricPlan", "MetricPlanner"]
