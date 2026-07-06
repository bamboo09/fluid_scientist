"""Tests for the MeasurementPlan and MetricPlanner modules."""

from __future__ import annotations

import pytest

from fluid_scientist.measurement.models import (
    FunctionObjectSpec,
    FunctionObjectType,
    MeasurementPlan,
    TimeSamplingSpec,
)
from fluid_scientist.measurement.planner import MetricPlan, MetricPlanner
from fluid_scientist.metric_spec.sampling import (
    DOEConfig,
    DOEPlan,
    SamplingConfig,
    SamplingPlan,
    SamplingStrategy,
    generate_doe_plan,
    generate_sampling_plan,
)

# --- Test fixtures ---


@pytest.fixture
def planner() -> MetricPlanner:
    return MetricPlanner()


def _make_spec():
    """Minimal ExperimentSpec for DOEPlan tests."""
    from fluid_scientist.experiment_spec.models import (
        ExperimentSpec,
        ParameterConstraints,
        ParameterSource,
        ParameterSourceInfo,
        ParameterSpec,
        ResearchSpec,
    )

    return ExperimentSpec(
        experiment_id="test-doe",
        research=ResearchSpec(title="DOE Test", objective="Test DOE plan"),
        parameters=[
            ParameterSpec(
                parameter_id="velocity",
                display_name="Velocity",
                category="bc",
                value=1.0,
                unit="m/s",
                source=ParameterSourceInfo(type=ParameterSource.USER),
                constraints=ParameterConstraints(min=0.5, max=2.0),
            ),
        ],
    )


# --- MetricPlanner tests ---


class TestMetricPlannerPressureDrop:
    def test_metric_planner_generates_pressure_drop_plan(self, planner: MetricPlanner):
        """压降指标生成 surfaceFieldValue functionObjects."""
        plan = planner.propose_metrics(
            research_objective="研究管内压降",
            user_metrics=["pressure_drop"],
            experiment_type="laminar_pipe",
        )

        mp = plan.measurement_plan
        # 入口和出口截面
        surface_ids = {s.id for s in mp.spatial_sampling}
        assert "inlet_section" in surface_ids
        assert "outlet_section" in surface_ids

        # surfaceFieldValue functionObjects
        fo_types = {fo.type for fo in mp.function_objects}
        assert FunctionObjectType.SURFACE_FIELD_VALUE in fo_types

        # pressure_drop 绑定存在
        binding_ids = {b.metric_id for b in mp.metric_bindings}
        assert "pressure_drop" in binding_ids

        # 绑定引用了具体数据源
        pd_binding = next(b for b in mp.metric_bindings if b.metric_id == "pressure_drop")
        assert pd_binding.source == "outlet_section"
        assert pd_binding.function_object is not None
        assert pd_binding.function_object.startswith("pressure_")

        # 所有 surfaceFieldValue 引用了 spatial_sampling id
        for fo in mp.function_objects:
            if fo.type == FunctionObjectType.SURFACE_FIELD_VALUE:
                assert fo.surface is not None
                assert fo.surface in surface_ids


class TestMetricPlannerForceCoeffs:
    def test_metric_planner_generates_force_coeffs(self, planner: MetricPlanner):
        """阻力/升力指标生成 forceCoeffs。"""
        plan = planner.propose_metrics(
            research_objective="研究圆柱绕流阻力升力",
            user_metrics=["drag_coefficient", "lift_coefficient"],
            experiment_type="cylinder_flow",
        )

        mp = plan.measurement_plan
        fo_types = {fo.type for fo in mp.function_objects}
        assert FunctionObjectType.FORCE_COEFFS in fo_types

        # forceCoeffs 应使用 cylinder patch
        fc = next(fo for fo in mp.function_objects if fo.type == FunctionObjectType.FORCE_COEFFS)
        assert fc.target_patch == "cylinder"

        binding_ids = {b.metric_id for b in mp.metric_bindings}
        assert "drag_coefficient" in binding_ids
        assert "lift_coefficient" in binding_ids

        # 两个指标应绑定到同一个 forceCoeffs
        drag_b = next(b for b in mp.metric_bindings if b.metric_id == "drag_coefficient")
        lift_b = next(b for b in mp.metric_bindings if b.metric_id == "lift_coefficient")
        assert drag_b.function_object == lift_b.function_object


class TestMetricPlannerStrouhal:
    def test_metric_planner_generates_strouhal_plan(self, planner: MetricPlanner):
        """Strouhal 数生成时间序列采样（更密集）。"""
        plan = planner.propose_metrics(
            research_objective="研究涡街频率",
            user_metrics=["strouhal_number"],
            experiment_type="cylinder_flow",
        )

        mp = plan.measurement_plan
        # 需要 forceCoeffs 来提取时序力
        fo_types = {fo.type for fo in mp.function_objects}
        assert FunctionObjectType.FORCE_COEFFS in fo_types

        # Strouhal 需要更密集的时间采样
        assert mp.time_sampling.interval <= 0.005
        assert mp.time_sampling.end_time >= 200.0

        binding_ids = {b.metric_id for b in mp.metric_bindings}
        assert "strouhal_number" in binding_ids


class TestMetricPlannerVelocityUniformity:
    def test_metric_planner_generates_velocity_uniformity(self, planner: MetricPlanner):
        """出口速度均匀性生成出口截面速度采样。"""
        plan = planner.propose_metrics(
            research_objective="研究出口速度均匀性",
            user_metrics=["outlet_velocity_uniformity"],
            experiment_type="laminar_pipe",
        )

        mp = plan.measurement_plan
        surface_ids = {s.id for s in mp.spatial_sampling}
        assert "outlet_uniformity_section" in surface_ids

        # 应有 surfaceFieldValue 测量 U
        vel_fos = [
            fo for fo in mp.function_objects
            if fo.type == FunctionObjectType.SURFACE_FIELD_VALUE and fo.field == "U"
        ]
        assert len(vel_fos) >= 1

        binding_ids = {b.metric_id for b in mp.metric_bindings}
        assert "outlet_velocity_uniformity" in binding_ids


class TestMetricPlannerUnknownMetrics:
    def test_metric_planner_identifies_unknown_metrics(self, planner: MetricPlanner):
        """未知指标被识别。"""
        plan = planner.propose_metrics(
            research_objective="研究某未知指标",
            user_metrics=["totally_unknown_metric"],
            experiment_type="laminar_pipe",
        )

        assert "totally_unknown_metric" in plan.unknown_metrics

    def test_known_standard_metric_not_unknown(self, planner: MetricPlanner):
        """标准指标（即使不在 registry）不应被标记为未知。"""
        plan = planner.propose_metrics(
            research_objective="研究",
            user_metrics=["drag_coefficient"],
            experiment_type="laminar_pipe",  # pipe registry 没有 drag
        )

        # drag_coefficient 是标准指标，不应在 unknown 中
        assert "drag_coefficient" not in plan.unknown_metrics


class TestMeasurementPlanFields:
    def test_measurement_plan_has_required_fields(self, planner: MetricPlanner):
        """必要场变量（U, p）存在。"""
        plan = planner.propose_metrics(
            research_objective="研究",
            user_metrics=["pressure_drop"],
            experiment_type="laminar_pipe",
        )

        field_names = {f.field_name for f in plan.measurement_plan.required_fields}
        assert "U" in field_names
        assert "p" in field_names

    def test_empty_measurement_plan(self):
        """空 MeasurementPlan 仍可构造且有默认值。"""
        mp = MeasurementPlan()
        assert mp.required_fields == []
        assert mp.function_objects == []
        assert mp.spatial_sampling == []
        assert mp.metric_bindings == []
        assert isinstance(mp.time_sampling, TimeSamplingSpec)


class TestDOEPlanRename:
    def test_doe_plan_renamed_from_sampling_plan(self):
        """SamplingPlan 别名仍可用且等价于 DOEPlan。"""
        assert SamplingPlan is DOEPlan
        assert SamplingConfig is DOEConfig
        assert generate_sampling_plan is generate_doe_plan

    def test_doe_plan_via_alias(self):
        """通过旧别名创建的 plan 与新名称行为一致。"""
        spec = _make_spec()
        plan_new = generate_doe_plan(spec, DOEConfig(strategy=SamplingStrategy.OAT))
        plan_old = generate_sampling_plan(spec, SamplingConfig(strategy=SamplingStrategy.OAT))

        assert isinstance(plan_new, DOEPlan)
        assert isinstance(plan_old, SamplingPlan)
        assert isinstance(plan_old, DOEPlan)
        assert plan_new.strategy == plan_old.strategy
        assert plan_new.num_samples == plan_old.num_samples
        assert plan_new.samples == plan_old.samples

    def test_doe_config_validation(self):
        """DOEConfig 验证规则与 SamplingConfig 一致。"""
        with pytest.raises(ValueError):
            DOEConfig(levels=1)
        with pytest.raises(ValueError):
            DOEConfig(num_samples=0)

    def test_doe_plan_no_design_variables(self):
        """无设计变量时返回单一 baseline 样本。"""
        from fluid_scientist.experiment_spec.models import (
            ExperimentSpec,
            ParameterSource,
            ParameterSourceInfo,
            ParameterSpec,
            ResearchSpec,
        )

        spec = ExperimentSpec(
            experiment_id="no-vars",
            research=ResearchSpec(title="No Vars", objective="No variables to test"),
            parameters=[
                ParameterSpec(
                    parameter_id="x",
                    display_name="X",
                    category="c",
                    value=1.0,
                    source=ParameterSourceInfo(type=ParameterSource.USER),
                ),
            ],
        )
        plan = generate_doe_plan(spec)
        assert isinstance(plan, DOEPlan)
        assert plan.num_samples == 1


class TestMetricPlannerCategorization:
    def test_metric_planner_categorizes_metrics(self, planner: MetricPlanner):
        """指标正确分类（core/credibility/extension）。"""
        plan = planner.propose_metrics(
            research_objective="研究圆柱绕流",
            user_metrics=["drag_coefficient"],
            experiment_type="cylinder_flow",
        )

        # drag_coefficient 是 critical + 用户请求 → core
        assert "drag_coefficient" in plan.core_metrics

        # residual_tolerance 是 convergence → credibility
        assert "residual_tolerance" in plan.credibility_metrics

        # lift_coefficient / strouhal_number / pressure_drop 是 extension
        # （未请求且非 critical、非 convergence）
        assert "lift_coefficient" in plan.extension_metrics
        assert "strouhal_number" in plan.extension_metrics

    def test_categorization_no_registry_match(self, planner: MetricPlanner):
        """无 registry 匹配时，用户指标仍进入 core，其余为空。"""
        plan = planner.propose_metrics(
            research_objective="研究",
            user_metrics=["pressure_drop"],
            experiment_type="unknown_type",
        )

        assert "pressure_drop" in plan.core_metrics
        assert plan.credibility_metrics == []
        assert plan.extension_metrics == []


class TestMetricPlanModel:
    def test_metric_plan_defaults(self):
        """MetricPlan 可使用默认值构造。"""
        mp = MetricPlan(measurement_plan=MeasurementPlan())
        assert mp.core_metrics == []
        assert mp.credibility_metrics == []
        assert mp.extension_metrics == []
        assert mp.unknown_metrics == []
        assert isinstance(mp.measurement_plan, MeasurementPlan)

    def test_every_metric_has_binding(self, planner: MetricPlanner):
        """每个核心指标都绑定到具体数据源。"""
        plan = planner.propose_metrics(
            research_objective="研究",
            user_metrics=["pressure_drop", "drag_coefficient", "strouhal_number"],
            experiment_type="cylinder_flow",
        )

        mp = plan.measurement_plan
        binding_metric_ids = {b.metric_id for b in mp.metric_bindings}
        for metric_id in ("pressure_drop", "drag_coefficient", "strouhal_number"):
            assert metric_id in binding_metric_ids, (
                f"metric '{metric_id}' has no MetricBinding"
            )

    def test_function_objects_not_empty(self, planner: MetricPlanner):
        """MetricPlanner 必须实际生成 functionObject 配置（不是空壳）。"""
        plan = planner.propose_metrics(
            research_objective="研究",
            user_metrics=["pressure_drop", "drag_coefficient"],
            experiment_type="cylinder_flow",
        )

        assert len(plan.measurement_plan.function_objects) > 0
        for fo in plan.measurement_plan.function_objects:
            assert isinstance(fo, FunctionObjectSpec)
            assert fo.type in FunctionObjectType
