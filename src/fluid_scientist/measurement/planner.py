"""MetricPlanner — generate metric plans and measurement plans from research goals.

The ``MetricPlanner`` bridges the research intent (what the user wants to know)
and the simulation configuration (what to sample inside a single run).  It
produces a :class:`MetricPlan` that categorises requested metrics into
core / credibility / comparison / extension / optional buckets and a
:class:`MeasurementPlan` that materialises the corresponding OpenFOAM
functionObjects, spatial sampling locations, field outputs and time-sampling
strategy.

The planning pipeline follows:

1. **Physical quantity decomposition** -- from ``research_objective`` and
   ``physics_spec`` determine which physical quantities are relevant.
2. **Unknown metric extraction** -- non-standard metric names from natural
   language are captured as :class:`UnknownMetric` objects.
3. **Metric classification**:
   * **core** -- directly answers the user's research question.
   * **credibility** -- convergence/numerical quality (residuals, mass
     conservation, Courant).
   * **comparison** -- for comparing different models/cases (reynolds_number,
     friction_factor, pressure_coefficient).
   * **extension** -- helpful but not critical.
   * **optional** -- nice-to-have.
4. **Metric definitions** -- for each known metric, store formula, unit, and
   category in ``metric_definitions``.
5. **Required data** -- generate :class:`MeasurementPlan` with the sampling
   configuration needed to extract the selected metrics.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

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


class UnknownMetric(BaseModel):
    """未知指标的结构化记录。"""

    metric_name: str
    registry_match: str | None = None
    status: Literal["unknown", "pending_lookup", "awaiting_code_approval"] = "unknown"
    user_requested: bool = True
    extraction_source: str | None = None  # which user message mentioned it


class MetricPlan(BaseModel):
    """指标计划。"""

    core_metrics: list[str] = Field(default_factory=list)
    credibility_metrics: list[str] = Field(default_factory=list)
    comparison_metrics: list[str] = Field(default_factory=list)
    extension_metrics: list[str] = Field(default_factory=list)
    optional_metrics: list[str] = Field(default_factory=list)
    unknown_metrics: list[str] = Field(default_factory=list)
    unknown_metric_details: list[UnknownMetric] = Field(default_factory=list)
    metric_definitions: dict[str, dict[str, Any]] = Field(default_factory=dict)
    measurement_plan: MeasurementPlan
    reasoning_summary: str = ""


class MetricPlanner:
    """从研究目标和物理规格生成指标计划。"""

    # Standard metric definitions with formulas
    _METRIC_DEFINITIONS: dict[str, dict[str, Any]] = {
        "pressure_drop": {
            "formula": "p_inlet - p_outlet",
            "unit": "Pa",
            "category": "physical",
            "display_name": "压降",
            "data_type": "scalar",
        },
        "drag_coefficient": {
            "formula": "Fd / (0.5 * rho * U^2 * A)",
            "unit": "dimensionless",
            "category": "physical",
            "display_name": "阻力系数 Cd",
            "data_type": "scalar",
        },
        "lift_coefficient": {
            "formula": "Fl / (0.5 * rho * U^2 * A)",
            "unit": "dimensionless",
            "category": "physical",
            "display_name": "升力系数 Cl",
            "data_type": "scalar",
        },
        "strouhal_number": {
            "formula": "f * D / U",
            "unit": "dimensionless",
            "category": "dimensionless",
            "display_name": "Strouhal 数 St",
            "data_type": "scalar",
        },
        "friction_factor": {
            "formula": "dp / (0.5 * rho * U^2 * L / D)",
            "unit": "dimensionless",
            "category": "dimensionless",
            "display_name": "摩擦系数 f",
            "data_type": "scalar",
        },
        "reynolds_number": {
            "formula": "rho * U * D / mu",
            "unit": "dimensionless",
            "category": "dimensionless",
            "display_name": "Reynolds 数",
            "data_type": "scalar",
        },
        "velocity_profile": {
            "formula": "U(x, y, z) at cross-sections",
            "unit": "m/s",
            "category": "physical",
            "display_name": "速度剖面",
            "data_type": "vector",
        },
        "outlet_velocity_uniformity": {
            "formula": "CV_u = sigma_u / mean_u",
            "unit": "dimensionless",
            "category": "physical",
            "display_name": "出口速度均匀性",
            "data_type": "scalar",
        },
        "pressure_coefficient": {
            "formula": "Cp = (p - p_inf) / (0.5 * rho * U^2)",
            "unit": "dimensionless",
            "category": "dimensionless",
            "display_name": "压力系数 Cp",
            "data_type": "scalar",
        },
        "mass_flow_rate": {
            "formula": "m_dot = rho * U * A",
            "unit": "kg/s",
            "category": "physical",
            "display_name": "质量流量",
            "data_type": "scalar",
        },
        "residual_tolerance": {
            "formula": "max(initial_residuals)",
            "unit": "dimensionless",
            "category": "convergence",
            "display_name": "残差容差",
            "data_type": "scalar",
        },
        "vortex_center_x": {
            "formula": "argmin(|velocity|) along x",
            "unit": "m",
            "category": "physical",
            "display_name": "涡心 X 坐标",
            "data_type": "scalar",
        },
        "vortex_center_y": {
            "formula": "argmin(|velocity|) along y",
            "unit": "m",
            "category": "physical",
            "display_name": "涡心 Y 坐标",
            "data_type": "scalar",
        },
        "pressure_profile": {
            "formula": "p(x, y, z) along centerlines",
            "unit": "Pa",
            "category": "physical",
            "display_name": "压力剖面",
            "data_type": "vector",
        },
    }

    # Metrics that serve as comparison/reference
    _COMPARISON_METRICS = {"reynolds_number", "friction_factor", "pressure_coefficient"}

    # Metrics that are credibility/numerical quality
    _CREDIBILITY_METRICS = {"residual_tolerance"}

    def propose_metrics(
        self,
        research_objective: str,
        physics_spec: ResearchPhysicsSpec | None = None,
        user_metrics: list[str] | None = None,
        experiment_type: str = "unknown",
    ) -> MetricPlan:
        """提议指标并生成测量计划。

        规划流程：
        1. 物理量分解 -- 基于 research_objective 和 physics_spec 确定相关物理量。
        2. 未知指标提取 -- 将非标准指标名提取为 :class:`UnknownMetric` 对象。
        3. 指标分类 -- core / credibility / comparison / extension / optional。
        4. 指标定义 -- 为每个已知指标存储公式、单位、类别。
        5. 生成 :class:`MeasurementPlan`。

        Args:
            research_objective: 研究目标描述（用于指标推理和未知指标溯源）。
            physics_spec: 研究物理规格，可选。
            user_metrics: 用户显式请求的指标 ID 列表。
            experiment_type: 实验类型，用于匹配 registry 中的标准指标。

        Returns:
            包含指标分类、定义和测量计划的 :class:`MetricPlan`。
        """
        user_metrics = user_metrics or []
        core_metrics: list[str] = []
        credibility_metrics: list[str] = []
        comparison_metrics: list[str] = []
        extension_metrics: list[str] = []
        optional_metrics: list[str] = []
        unknown_metrics: list[str] = []
        unknown_details: list[UnknownMetric] = []
        metric_definitions: dict[str, dict[str, Any]] = {}
        reasoning_parts: list[str] = []

        user_set = set(user_metrics)

        # 1. 从 registry 获取标准指标，进行分类并存储定义
        #    CONVERGENCE 类指标（如 residual_tolerance）优先归入 credibility，
        #    即使被标记为 critical 也不应进入 core。
        try:
            registry_spec = get_metric_spec(experiment_type)
            for metric_def in registry_spec.metrics:
                mid = metric_def.metric_id
                # 存储指标定义
                metric_definitions[mid] = {
                    "formula": metric_def.formula,
                    "unit": metric_def.unit,
                    "category": metric_def.category.value,
                    "display_name": metric_def.display_name,
                    "data_type": metric_def.data_type.value,
                    "critical": metric_def.critical,
                }

                if (
                    metric_def.category == MetricCategory.CONVERGENCE
                    or mid in self._CREDIBILITY_METRICS
                ):
                    credibility_metrics.append(mid)
                elif mid in user_set or metric_def.critical:
                    core_metrics.append(mid)
                elif mid in self._COMPARISON_METRICS:
                    comparison_metrics.append(mid)
                else:
                    extension_metrics.append(mid)
        except Exception:
            # 无匹配的 registry spec — 仅依赖用户指标
            pass

        # 2. 补充分类：用户请求的指标若尚未被 registry 分类，则按是否为
        #    标准指标归入 core（已知标准指标）或 unknown（完全未知）。
        known_metrics = set(
            core_metrics + credibility_metrics + comparison_metrics + extension_metrics
        )
        for m in user_metrics:
            if m in known_metrics:
                continue
            if m in self._METRIC_DEFINITIONS:
                # 已知标准指标但不在 registry 中
                core_metrics.append(m)
                metric_definitions[m] = self._METRIC_DEFINITIONS[m]
                known_metrics.add(m)
            else:
                # 未知指标 -- 提取为结构化记录
                unknown_metrics.append(m)
                unknown_details.append(
                    UnknownMetric(
                        metric_name=m,
                        registry_match=None,
                        status="unknown",
                        user_requested=True,
                        extraction_source=(
                            research_objective[:200] if research_objective else None
                        ),
                    )
                )

        # 3. 补充对比指标（若尚未包含且物理规格中存在流态信息）
        for cm in self._COMPARISON_METRICS:
            if cm not in known_metrics and cm in self._METRIC_DEFINITIONS:
                # 当流态相关时添加 reynolds_number 作为对比指标
                if cm == "reynolds_number" and physics_spec and physics_spec.flow_regime:
                    comparison_metrics.append(cm)
                    metric_definitions[cm] = self._METRIC_DEFINITIONS[cm]
                    known_metrics.add(cm)

        # 4. 生成推理摘要
        if core_metrics:
            reasoning_parts.append(
                f"核心指标: {', '.join(core_metrics)} — 直接回答研究问题"
            )
        if credibility_metrics:
            reasoning_parts.append(
                f"可信度指标: {', '.join(credibility_metrics)} — 验证数值结果可靠性"
            )
        if comparison_metrics:
            reasoning_parts.append(
                f"对比指标: {', '.join(comparison_metrics)} — 用于工况/模型对比"
            )
        if extension_metrics:
            reasoning_parts.append(
                f"扩展指标: {', '.join(extension_metrics)} — 有帮助但非必要"
            )
        if unknown_metrics:
            reasoning_parts.append(
                f"未知指标: {', '.join(unknown_metrics)} — 需要代码扩展或用户澄清"
            )

        reasoning_summary = (
            "；".join(reasoning_parts) if reasoning_parts else "无指标分类信息"
        )

        # 5. 生成 MeasurementPlan（用户指标 + 核心 registry 指标）
        plan_metrics = list(dict.fromkeys(core_metrics + user_metrics))
        measurement_plan = self._generate_measurement_plan(plan_metrics, experiment_type)

        return MetricPlan(
            core_metrics=core_metrics,
            credibility_metrics=credibility_metrics,
            comparison_metrics=comparison_metrics,
            extension_metrics=extension_metrics,
            optional_metrics=optional_metrics,
            unknown_metrics=unknown_metrics,
            unknown_metric_details=unknown_details,
            metric_definitions=metric_definitions,
            measurement_plan=measurement_plan,
            reasoning_summary=reasoning_summary,
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


__all__ = ["MetricPlan", "MetricPlanner", "UnknownMetric"]
