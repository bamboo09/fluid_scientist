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
2. **Keyword-based metric inference** -- parse ``research_objective`` text
   for Chinese/English keywords that map to known metric IDs (e.g. "压降" ->
   pressure_drop, "阻力" -> drag_coefficient).
3. **Unknown metric extraction** -- non-standard metric names from natural
   language are captured as :class:`UnknownMetric` objects.
4. **Metric classification**:
   * **core** -- directly answers the user's research question.
   * **credibility** -- convergence/numerical quality (residuals, mass
     conservation, Courant).
   * **comparison** -- for comparing different models/cases (reynolds_number,
     friction_factor, pressure_coefficient).
   * **extension** -- helpful but not critical.
   * **optional** -- nice-to-have.
5. **Metric definitions** -- for each known metric, store formula, unit,
   category, required_data, and quality_checks in ``metric_definitions``.
6. **Required data** -- generate :class:`MeasurementPlan` with the sampling
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
    StorageEstimate,
    TimeSamplingSpec,
)
from fluid_scientist.measurement.time_sampler import (
    PhysicalContext,
    TimeSampler,
    estimate_vortex_shedding_frequency,
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

    # Chinese/English keyword -> metric_id mapping for objective parsing
    _OBJECTIVE_KEYWORD_MAP: dict[str, str] = {
        "压降": "pressure_drop",
        "压力降": "pressure_drop",
        "压力损失": "pressure_drop",
        "阻力": "drag_coefficient",
        "阻力系数": "drag_coefficient",
        "升力": "lift_coefficient",
        "升力系数": "lift_coefficient",
        "涡脱落": "strouhal_number",
        "涡街": "strouhal_number",
        "卡门涡": "strouhal_number",
        "strouhal": "strouhal_number",
        "速度均匀性": "outlet_velocity_uniformity",
        "出口均匀性": "outlet_velocity_uniformity",
        "壁面剪应力": "wall_shear_stress",
        "壁面剪切": "wall_shear_stress",
        "摩擦系数": "friction_factor",
        "摩擦": "friction_factor",
        "速度剖面": "velocity_profile",
        "速度场": "velocity_profile",
        "压力剖面": "pressure_profile",
        "压力场": "pressure_profile",
        "压力系数": "pressure_coefficient",
        "涡心": "vortex_center_x",
        "压力脉动": "pressure_rms",
        "压力波动": "pressure_rms",
        "速度脉动": "velocity_rms",
        "速度波动": "velocity_rms",
        "湍流强度": "velocity_rms",
        "二次流": "secondary_flow_intensity",
        "旋流": "swirl_number",
        "旋转流": "swirl_number",
        "频谱": "frequency_spectrum_peak",
        "功率谱": "frequency_spectrum_peak",
        "统计收敛": "statistical_stability",
        "统计稳定性": "statistical_stability",
    }

    # Standard metric definitions with formulas, required_data, quality_checks
    _METRIC_DEFINITIONS: dict[str, dict[str, Any]] = {
        "pressure_drop": {
            "formula": "p_inlet - p_outlet",
            "unit": "Pa",
            "category": "physical",
            "display_name": "压降",
            "data_type": "scalar",
            "required_data": [
                "inlet/outlet surfaceFieldValue",
                "inlet_boundary_pressure",
                "outlet_boundary_pressure",
            ],
            "quality_checks": [
                "mass_balance",
                "statistical_convergence",
            ],
        },
        "drag_coefficient": {
            "formula": "Fd / (0.5 * rho * U^2 * A)",
            "unit": "dimensionless",
            "category": "physical",
            "display_name": "阻力系数 Cd",
            "data_type": "scalar",
            "required_data": [
                "forceCoeffs time series",
                "cylinder_diameter",
                "inlet_velocity",
                "fluid_density",
            ],
            "quality_checks": [
                "sampling_frequency",
                "minimum_cycles",
                "statistical_convergence",
            ],
        },
        "lift_coefficient": {
            "formula": "Fl / (0.5 * rho * U^2 * A)",
            "unit": "dimensionless",
            "category": "physical",
            "display_name": "升力系数 Cl",
            "data_type": "scalar",
            "required_data": [
                "forceCoeffs time series",
                "cylinder_diameter",
                "inlet_velocity",
                "fluid_density",
            ],
            "quality_checks": [
                "sampling_frequency",
                "minimum_cycles",
                "statistical_convergence",
            ],
        },
        "strouhal_number": {
            "formula": "f * D / U",
            "unit": "dimensionless",
            "category": "dimensionless",
            "display_name": "Strouhal 数 St",
            "data_type": "scalar",
            "required_data": [
                "lift_coefficient time series",
                "cylinder_diameter",
                "inlet_velocity",
            ],
            "quality_checks": [
                "sampling_frequency",
                "minimum_cycles",
                "peak_prominence",
            ],
        },
        "friction_factor": {
            "formula": "dp / (0.5 * rho * U^2 * L / D)",
            "unit": "dimensionless",
            "category": "dimensionless",
            "display_name": "摩擦系数 f",
            "data_type": "scalar",
            "required_data": [
                "pressure_drop",
                "pipe_diameter",
                "pipe_length",
                "mean_velocity",
                "fluid_density",
            ],
            "quality_checks": [
                "reynolds_number_range",
                "statistical_convergence",
            ],
        },
        "reynolds_number": {
            "formula": "rho * U * D / mu",
            "unit": "dimensionless",
            "category": "dimensionless",
            "display_name": "Reynolds 数",
            "data_type": "scalar",
            "required_data": [
                "pipe_diameter",
                "mean_velocity",
                "fluid_density",
                "fluid_viscosity",
            ],
            "quality_checks": [
                "flow_regime_consistency",
            ],
        },
        "velocity_profile": {
            "formula": "U(x, y, z) at cross-sections",
            "unit": "m/s",
            "category": "physical",
            "display_name": "速度剖面",
            "data_type": "vector",
            "required_data": [
                "probes along cross-sections",
                "axial_velocity_field",
            ],
            "quality_checks": [
                "statistical_convergence",
                "symmetry_check",
            ],
        },
        "outlet_velocity_uniformity": {
            "formula": "CV_u = sigma_u / mean_u",
            "unit": "dimensionless",
            "category": "physical",
            "display_name": "出口速度均匀性",
            "data_type": "scalar",
            "required_data": [
                "outlet surface velocity field",
                "outlet areaAverage(mag(U))",
                "outlet areaAverage(U)",
            ],
            "quality_checks": [
                "statistical_convergence",
            ],
        },
        "pressure_coefficient": {
            "formula": "Cp = (p - p_inf) / (0.5 * rho * U^2)",
            "unit": "dimensionless",
            "category": "dimensionless",
            "display_name": "压力系数 Cp",
            "data_type": "scalar",
            "required_data": [
                "surface pressure distribution",
                "reference_pressure",
                "inlet_velocity",
                "fluid_density",
            ],
            "quality_checks": [
                "statistical_convergence",
            ],
        },
        "mass_flow_rate": {
            "formula": "m_dot = rho * U * A",
            "unit": "kg/s",
            "category": "physical",
            "display_name": "质量流量",
            "data_type": "scalar",
            "required_data": [
                "inlet/outlet surfaceFieldValue",
                "fluid_density",
                "cross_section_area",
            ],
            "quality_checks": [
                "mass_balance",
            ],
        },
        "residual_tolerance": {
            "formula": "max(initial_residuals)",
            "unit": "dimensionless",
            "category": "convergence",
            "display_name": "残差容差",
            "data_type": "scalar",
            "required_data": [
                "solver residual log",
            ],
            "quality_checks": [
                "residual_tolerance_threshold",
            ],
        },
        "vortex_center_x": {
            "formula": "argmin(|velocity|) along x",
            "unit": "m",
            "category": "physical",
            "display_name": "涡心 X 坐标",
            "data_type": "scalar",
            "required_data": [
                "velocity_field",
                "cavity_geometry",
            ],
            "quality_checks": [
                "statistical_convergence",
            ],
        },
        "vortex_center_y": {
            "formula": "argmin(|velocity|) along y",
            "unit": "m",
            "category": "physical",
            "display_name": "涡心 Y 坐标",
            "data_type": "scalar",
            "required_data": [
                "velocity_field",
                "cavity_geometry",
            ],
            "quality_checks": [
                "statistical_convergence",
            ],
        },
        "pressure_profile": {
            "formula": "p(x, y, z) along centerlines",
            "unit": "Pa",
            "category": "physical",
            "display_name": "压力剖面",
            "data_type": "vector",
            "required_data": [
                "probes along centerlines",
                "pressure_field",
            ],
            "quality_checks": [
                "statistical_convergence",
            ],
        },
        "wall_shear_stress": {
            "formula": "tau_w = mu * (dU/dy)_wall",
            "unit": "Pa",
            "category": "physical",
            "display_name": "壁面剪应力",
            "data_type": "scalar",
            "required_data": [
                "wallGradU functionObject",
                "fluid_viscosity",
                "wall_patch_ids",
            ],
            "quality_checks": [
                "statistical_convergence",
            ],
        },
        # --- Additional metrics (Change 3) ---
        "pressure_rms": {
            "formula": "sqrt(mean((p - mean(p))^2))",
            "unit": "Pa",
            "category": "physical",
            "display_name": "压力脉动 RMS",
            "data_type": "scalar",
            "required_data": [
                "pressure time series at probe points",
                "statistical sampling window",
            ],
            "quality_checks": [
                "sampling_frequency",
                "minimum_samples",
                "statistical_convergence",
            ],
        },
        "velocity_rms": {
            "formula": "sqrt(mean((u - mean(u))^2))",
            "unit": "m/s",
            "category": "physical",
            "display_name": "速度脉动 RMS",
            "data_type": "scalar",
            "required_data": [
                "velocity time series at probe points",
                "statistical sampling window",
            ],
            "quality_checks": [
                "sampling_frequency",
                "minimum_samples",
                "statistical_convergence",
            ],
        },
        "outlet_velocity_distortion": {
            "formula": "distortion = max(U) / mean(U) at outlet",
            "unit": "dimensionless",
            "category": "physical",
            "display_name": "出口速度畸变",
            "data_type": "scalar",
            "required_data": [
                "outlet surface velocity field",
                "outlet areaAverage(U)",
                "outlet max(U)",
            ],
            "quality_checks": [
                "statistical_convergence",
            ],
        },
        "secondary_flow_intensity": {
            "formula": "I_sf = sqrt(u_sec^2 + v_sec^2) / U_mean",
            "unit": "dimensionless",
            "category": "physical",
            "display_name": "二次流强度",
            "data_type": "scalar",
            "required_data": [
                "cross-plane velocity field",
                "mean axial velocity",
            ],
            "quality_checks": [
                "statistical_convergence",
            ],
        },
        "swirl_number": {
            "formula": "S = integral(rho * U_theta * r * U_z dA) / (R * integral(rho * U_z^2 dA))",
            "unit": "dimensionless",
            "category": "dimensionless",
            "display_name": "旋流数",
            "data_type": "scalar",
            "required_data": [
                "cross-plane velocity field",
                "tangential velocity profile",
                "axial velocity profile",
            ],
            "quality_checks": [
                "statistical_convergence",
            ],
        },
        "frequency_spectrum_peak": {
            "formula": "argmax(PSD(signal))",
            "unit": "Hz",
            "category": "physical",
            "display_name": "频谱主峰频率",
            "data_type": "scalar",
            "required_data": [
                "time series signal (forceCoeffs or probes)",
                "sampling_frequency",
            ],
            "quality_checks": [
                "sampling_frequency",
                "minimum_cycles",
                "peak_prominence",
            ],
        },
        "statistical_stability": {
            "formula": "CI = 1.96 * sigma / (sqrt(N) * mean)",
            "unit": "dimensionless",
            "category": "numerical",
            "display_name": "统计稳定性指标",
            "data_type": "scalar",
            "required_data": [
                "time series of target metric",
                "sample_count",
            ],
            "quality_checks": [
                "minimum_samples",
                "confidence_interval_threshold",
            ],
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
        2. 关键词推断 -- 从 research_objective 文本中解析指标关键词。
        3. 未知指标提取 -- 将非标准指标名提取为 :class:`UnknownMetric` 对象。
        4. 指标分类 -- core / credibility / comparison / extension / optional。
        5. 指标定义 -- 为每个已知指标存储公式、单位、类别、required_data、quality_checks。
        6. 生成 :class:`MeasurementPlan`。

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

        # 0. Parse research_objective for metric keywords
        inferred_metrics = self._infer_metrics_from_objective(research_objective)

        # Merge inferred metrics into user_metrics (avoid duplicates)
        for m in inferred_metrics:
            if m not in user_set:
                user_metrics.append(m)
                user_set.add(m)

        # 1. 从 registry 获取标准指标，进行分类并存储定义
        #    CONVERGENCE 类指标（如 residual_tolerance）优先归入 credibility，
        #    即使被标记为 critical 也不应进入 core。
        try:
            registry_spec = get_metric_spec(experiment_type)
            for metric_def in registry_spec.metrics:
                mid = metric_def.metric_id
                # 存储指标定义（包含 required_data 和 quality_checks）
                metric_definitions[mid] = {
                    "formula": metric_def.formula,
                    "unit": metric_def.unit,
                    "category": metric_def.category.value,
                    "display_name": metric_def.display_name,
                    "data_type": metric_def.data_type.value,
                    "critical": metric_def.critical,
                    "required_data": list(metric_def.required_data),
                    "quality_checks": list(metric_def.quality_checks),
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
                metric_definitions[m] = dict(self._METRIC_DEFINITIONS[m])
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
            # 当流态相关时添加 reynolds_number 作为对比指标
            if (
                cm not in known_metrics
                and cm in self._METRIC_DEFINITIONS
                and cm == "reynolds_number"
                and physics_spec
                and physics_spec.flow_regime
            ):
                comparison_metrics.append(cm)
                metric_definitions[cm] = dict(self._METRIC_DEFINITIONS[cm])
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

        # Extract physics parameters for time sampling
        physics_params: dict[str, Any] = {}
        if physics_spec:
            if physics_spec.geometry_facts:
                physics_params["diameter"] = physics_spec.geometry_facts.get("diameter")
                physics_params["length"] = physics_spec.geometry_facts.get("length")
            if physics_spec.operating_conditions:
                physics_params["velocity"] = physics_spec.operating_conditions.get("inlet_velocity")
            if physics_spec.material_facts:
                physics_params["kinematic_viscosity"] = (
                    physics_spec.material_facts.get("kinematic_viscosity")
                )
            physics_params["is_transient"] = (
                physics_spec.temporal_type == "transient"
                if physics_spec.temporal_type
                else True
            )

        measurement_plan = self._generate_measurement_plan(
            plan_metrics, experiment_type, physics_params=physics_params,
            spec=physics_spec,
        )

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

    @classmethod
    def _infer_metrics_from_objective(cls, research_objective: str) -> list[str]:
        """从研究目标文本中推断指标 ID。

        解析 research_objective 中的中文/英文关键词，将其映射到已知指标 ID。
        例如："压降" -> pressure_drop, "阻力" -> drag_coefficient,
        "涡脱落" -> strouhal_number。
        """
        if not research_objective:
            return []
        inferred: list[str] = []
        seen: set[str] = set()
        for keyword, metric_id in cls._OBJECTIVE_KEYWORD_MAP.items():
            if keyword in research_objective and metric_id not in seen:
                inferred.append(metric_id)
                seen.add(metric_id)
        return inferred

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
            "wall_shear_stress",
            "pressure_rms",
            "velocity_rms",
            "outlet_velocity_distortion",
            "secondary_flow_intensity",
            "swirl_number",
            "frequency_spectrum_peak",
            "statistical_stability",
        }
        return metric_id in standard

    @staticmethod
    def _generate_measurement_plan(
        metrics: list[str],
        experiment_type: str,
        physics_params: dict[str, Any] | None = None,
        spec: Any | None = None,
    ) -> MeasurementPlan:
        """根据指标列表生成测量计划。

        Args:
            metrics: List of metric IDs to plan measurements for.
            experiment_type: Type of experiment (e.g. "cylinder_flow").
            physics_params: Physical parameters for time sampling.
            spec: The ExperimentSpec object, used to extract geometry
                parameters for generating real probe coordinates and
                surface definitions.
        """
        metric_set = set(metrics)
        physics_params = physics_params or {}

        # --- Extract geometry parameters from spec for real coordinates ---
        # spec is a ResearchPhysicsSpec with geometry_facts, operating_conditions,
        # material_facts dicts.  We merge all into a flat geom dict.
        geom: dict[str, float] = {}
        if spec is not None:
            for source_dict_name in ("geometry_facts", "operating_conditions",
                                     "material_facts", "boundary_facts"):
                source_dict = getattr(spec, source_dict_name, None)
                if source_dict and isinstance(source_dict, dict):
                    for key, val in source_dict.items():
                        if val is not None:
                            try:
                                geom[key] = float(val)
                            except (TypeError, ValueError):
                                pass

        diameter = geom.get("diameter", 1.0)
        length = geom.get("length", 1.0)
        side_length = geom.get("side_length", 1.0)
        domain_width = geom.get("domain_width", 10.0)
        domain_height = geom.get("domain_height", 10.0)
        extrusion_span = geom.get("extrusion_span", diameter * 0.1)

        required_fields = [
            FieldOutputSpec(field_name="U", write_interval=100),
            FieldOutputSpec(field_name="p", write_interval=100),
        ]
        function_objects: list[FunctionObjectSpec] = []
        spatial_sampling: list[SpatialSamplingSpec] = []
        metric_bindings: list[MetricBinding] = []
        probes: list[Any] = []

        # Helper: generate surface location dict based on experiment type
        def _surface_location(experiment_type: str, surface_name: str) -> dict[str, Any]:
            """Generate basePoint, normal, fields, surfaceFormat for a surface."""
            if experiment_type == "laminar_pipe":
                if surface_name == "inlet":
                    return {
                        "basePoint": [0.0, 0.0, 0.0],
                        "normal": [0.0, 0.0, -1.0],
                        "fields": ["p", "U"],
                        "surfaceFormat": "raw",
                        "writeInterval": 100,
                    }
                else:  # outlet
                    return {
                        "basePoint": [0.0, 0.0, length],
                        "normal": [0.0, 0.0, 1.0],
                        "fields": ["p", "U"],
                        "surfaceFormat": "raw",
                        "writeInterval": 100,
                    }
            elif experiment_type == "cylinder_flow":
                if "outlet" in surface_name:
                    upstream = geom.get("domain_upstream", 10.0) * diameter
                    return {
                        "basePoint": [upstream + diameter, 0.0, 0.0],
                        "normal": [1.0, 0.0, 0.0],
                        "fields": ["p", "U"],
                        "surfaceFormat": "raw",
                        "writeInterval": 100,
                    }
                return {
                    "basePoint": [0.0, 0.0, 0.0],
                    "normal": [1.0, 0.0, 0.0],
                    "fields": ["p", "U"],
                    "surfaceFormat": "raw",
                    "writeInterval": 100,
                }
            elif experiment_type == "lid_driven_cavity":
                return {
                    "basePoint": [0.0, 0.0, 0.0],
                    "normal": [0.0, -1.0, 0.0],
                    "fields": ["p", "U"],
                    "surfaceFormat": "raw",
                    "writeInterval": 100,
                }
            return {
                "fields": ["p", "U"],
                "surfaceFormat": "raw",
                "writeInterval": 100,
            }

        # 压降 → 入口/出口截面压力采样
        if "pressure_drop" in metric_set:
            inlet_id = "inlet_section"
            outlet_id = "outlet_section"
            spatial_sampling.extend([
                SpatialSamplingSpec(
                    id=inlet_id,
                    type=SpatialSamplingType.SURFACE,
                    description="入口截面",
                    location=_surface_location(experiment_type, "inlet"),
                ),
                SpatialSamplingSpec(
                    id=outlet_id,
                    type=SpatialSamplingType.SURFACE,
                    description="出口截面",
                    location=_surface_location(experiment_type, "outlet"),
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
            # Generate wake probes for cylinder flow
            if experiment_type == "cylinder_flow":
                from fluid_scientist.measurement.models import ProbeSpec
                upstream = geom.get("domain_upstream", 10.0) * diameter
                wake_x = upstream + 2.0 * diameter  # 2D downstream of cylinder center
                probe_positions = [
                    {"x": wake_x, "y": 0.0, "z": 0.0},
                    {"x": wake_x + diameter, "y": 0.5 * diameter, "z": 0.0},
                    {"x": wake_x + diameter, "y": -0.5 * diameter, "z": 0.0},
                    {"x": wake_x + 2 * diameter, "y": 0.0, "z": 0.0},
                ]
                probes.append(ProbeSpec(
                    id="wake_probes",
                    field="U",
                    positions=probe_positions,
                    write_interval=1,
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
        # CV = sigma_u / mean_u = sqrt(mean(U^2) - mean(U)^2) / mean(U)
        # 需要 areaAverage(U) 和 areaAverage(mag(U)) 来计算 CV
        if "outlet_velocity_uniformity" in metric_set:
            outlet_id = "outlet_uniformity_section"
            spatial_sampling.append(SpatialSamplingSpec(
                id=outlet_id,
                type=SpatialSamplingType.SURFACE,
                description="出口速度均匀性截面",
                location=_surface_location(experiment_type, "outlet"),
            ))
            # Need both average and RMS for CV calculation
            # CV = sigma_u / mean_u = sqrt(mean(U^2) - mean(U)^2) / mean(U)
            function_objects.append(FunctionObjectSpec(
                type=FunctionObjectType.SURFACE_FIELD_VALUE,
                name="velocity_outlet_mean",
                field="U",
                operation="areaAverage",
                surface=outlet_id,
            ))
            function_objects.append(FunctionObjectSpec(
                type=FunctionObjectType.SURFACE_FIELD_VALUE,
                name="velocity_outlet_magnitude",
                field="mag(U)",
                operation="areaAverage",
                surface=outlet_id,
            ))
            metric_bindings.append(MetricBinding(
                metric_id="outlet_velocity_uniformity",
                source=outlet_id,
                function_object="velocity_outlet_mean",
            ))

        # 速度剖面 → 线采样 + centerline probes
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
            # Generate centerline probes for pipe/cavity
            from fluid_scientist.measurement.models import ProbeSpec
            if experiment_type == "laminar_pipe":
                probe_positions = [
                    {"x": 0.0, "y": 0.0, "z": length * 0.25},
                    {"x": 0.0, "y": 0.0, "z": length * 0.50},
                    {"x": 0.0, "y": 0.0, "z": length * 0.75},
                ]
            elif experiment_type == "lid_driven_cavity":
                probe_positions = [
                    {"x": side_length * 0.5, "y": side_length * 0.25, "z": 0.0},
                    {"x": side_length * 0.5, "y": side_length * 0.50, "z": 0.0},
                    {"x": side_length * 0.5, "y": side_length * 0.75, "z": 0.0},
                ]
            else:
                probe_positions = [
                    {"x": 0.0, "y": 0.0, "z": 0.0},
                    {"x": 0.5, "y": 0.0, "z": 0.0},
                    {"x": 1.0, "y": 0.0, "z": 0.0},
                ]
            probes.append(ProbeSpec(
                id="centerline_probes",
                field="U",
                positions=probe_positions,
                write_interval=10,
            ))

        # 时间采样配置 — 动态计算或回退到默认值
        # Extract physical parameters for dynamic time sampling
        diameter = physics_params.get("diameter")
        velocity = physics_params.get("velocity")
        viscosity = physics_params.get("kinematic_viscosity")
        is_transient = physics_params.get("is_transient", True)
        char_length = diameter or physics_params.get("length")

        if velocity and char_length and velocity > 0 and char_length > 0:
            # Use dynamic time sampling based on physical characteristics
            estimated_freq = None
            if "strouhal_number" in metric_set and diameter:
                # Estimate vortex shedding frequency for Strouhal metrics
                reynolds = None
                if viscosity and viscosity > 0:
                    reynolds = velocity * diameter / viscosity
                estimated_freq = estimate_vortex_shedding_frequency(
                    diameter, velocity, reynolds
                )

            ctx = PhysicalContext(
                characteristic_length=char_length,
                characteristic_velocity=velocity,
                kinematic_viscosity=viscosity,
                estimated_frequency=estimated_freq,
                is_transient=is_transient,
            )
            time_sampling = TimeSampler().calculate(ctx)
        else:
            # Fall back to hardcoded defaults when no physics params available
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

        # Storage estimate
        estimated_bytes = 0
        breakdown: dict[str, int] = {}
        for fo in function_objects:
            fo_bytes = 1000 * 100  # rough estimate per timestep
            estimated_bytes += fo_bytes
            breakdown[f"fo_{fo.name}"] = fo_bytes
        for field in required_fields:
            field_bytes = 5000 * 100  # field output per timestep
            estimated_bytes += field_bytes
            breakdown[f"field_{field.field_name}"] = field_bytes
        num_timesteps = int(
            (time_sampling.end_time - time_sampling.start_time)
            / max(time_sampling.interval, 1e-10)
        )
        estimated_bytes *= max(num_timesteps, 1)

        storage_estimate = StorageEstimate(
            estimated_bytes=estimated_bytes,
            breakdown=breakdown,
            exceeds_budget=False,
            budget_bytes=None,
        )

        return MeasurementPlan(
            required_fields=required_fields,
            function_objects=function_objects,
            spatial_sampling=spatial_sampling,
            probes=probes,
            time_sampling=time_sampling,
            metric_bindings=metric_bindings,
            storage_estimate=storage_estimate,
        )


__all__ = ["MetricPlan", "MetricPlanner", "UnknownMetric"]
