"""从 Dynamic Schema 生成 ExperimentSpec 的工厂。"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from fluid_scientist.compat import UTC
from fluid_scientist.dynamic_schema.schema_engine import generate_schema
from fluid_scientist.experiment_spec.models import (
    Compressibility,
    Dimensions,
    ExperimentSpec,
    ExperimentStatus,
    FlowRegime,
    InteractionMode,
    PhaseType,
    PhysicsSpec,
    ResearchSpec,
    TaskType,
    TemporalType,
)
from fluid_scientist.research.models import (
    IntentAssessment,
    ResearchPhysicsSpec,
    ResearchSession,
)


def _to_float(value):
    """安全转换为 float。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (ValueError, TypeError):
        return None


class ExperimentSpecFactory:
    """从 Dynamic Schema 生成 ExperimentSpec。"""

    def create_from_schema(
        self,
        session: ResearchSession,
        intent: IntentAssessment,
        physics_spec: ResearchPhysicsSpec | None,
    ) -> ExperimentSpec:
        """从研究会话和物理规格生成实验规格。

        流程:
        1. 将 ResearchPhysicsSpec 转换为 experiment_spec.PhysicsSpec
        2. 从会话事实中提取已有参数值
        3. 调用 generate_schema() 生成参数 schema（含已有值）
        4. 构造 ExperimentSpec
        """
        # 1. 转换 PhysicsSpec
        esp_physics = self._convert_physics_spec(physics_spec)

        # 2. 从会话事实中提取参数值
        existing_params = self._extract_existing_params(session, physics_spec)

        # 3. 调用 Dynamic Schema Engine（传入已有参数值）
        schema_result = generate_schema(esp_physics, existing_params=existing_params)

        # 4. 构造 ExperimentSpec
        experiment_id = f"exp-{uuid4().hex[:16]}"
        now = datetime.now(UTC).isoformat()

        # 从 intent 提取研究信息
        research = ResearchSpec(
            title=intent.research_objective or session.original_request[:100],
            objective=intent.research_objective or session.original_request,
            hypothesis=None,
            comparison_target=intent.physical_system,
            user_questions=[session.original_request],
        )

        # 确定 task_type
        task_type = self._map_task_type(intent.task_type)

        spec = ExperimentSpec(
            experiment_id=experiment_id,
            schema_version="1.0.0",
            experiment_version=1,
            status=ExperimentStatus.DRAFT,
            task_type=task_type,
            interaction_mode=InteractionMode.STANDARD,
            research=research,
            physics=esp_physics,
            parameters=list(schema_result.parameters),
            metrics=[],  # 将在 Commit 5 填充
            created_at=now,
            updated_at=now,
        )

        return spec

    @staticmethod
    def _extract_existing_params(
        session: ResearchSession,
        physics_spec: ResearchPhysicsSpec | None,
    ) -> dict:
        """从会话的 confirmed_facts 和累积上下文中提取参数值。

        将用户在对话中提到的数值（如管径、流速、密度等）映射到
        Dynamic Schema Engine 的 existing_params 字典中。
        """
        params = {}

        # 从 confirmed_facts 中提取
        for fact in session.confirmed_facts:
            key = fact.key
            value = fact.value

            # 映射事实键到参数 ID
            key_lower = key.lower() if isinstance(key, str) else str(key).lower()

            # 直接匹配常见参数名
            if key_lower in ("diameter", "管径", "pipe_diameter"):
                params["diameter"] = _to_float(value)
            elif key_lower in ("length", "管长", "pipe_length"):
                params["length"] = _to_float(value)
            elif key_lower in ("inlet_velocity", "入口速度", "velocity", "流速"):
                params["inlet_velocity"] = _to_float(value)
            elif key_lower in ("mean_velocity", "平均速度"):
                params["mean_velocity"] = _to_float(value)
            elif key_lower in ("density", "密度", "fluid_density"):
                params["density"] = _to_float(value)
            elif key_lower in ("kinematic_viscosity", "运动粘度", "viscosity"):
                params["kinematic_viscosity"] = _to_float(value)
            elif key_lower in ("reynolds_number", "reynolds", "雷诺数"):
                params["reynolds_number"] = _to_float(value)
            elif key_lower in ("lid_velocity", "盖板速度"):
                params["lid_velocity"] = _to_float(value)
            elif key_lower in ("side_length", "边长"):
                params["side_length"] = _to_float(value)

        # 从原始请求中提取数值（正则匹配）
        import re

        full_text = session.accumulated_context.get("all_messages", "") or session.original_request

        # 管径: "管径0.05米" 或 "diameter 0.05m"
        m = re.search(r"管径\s*([0-9.]+)", full_text)
        if m and "diameter" not in params:
            params["diameter"] = float(m.group(1))

        # 流速: "流速0.02米每秒" 或 "流速 0.02 m/s"
        m = re.search(r"流速\s*([0-9.]+)", full_text)
        if m and "inlet_velocity" not in params:
            params["inlet_velocity"] = float(m.group(1))

        # 密度: "密度1000" 或 "density 1000"
        m = re.search(r"密度\s*([0-9.]+)", full_text)
        if m and "density" not in params:
            params["density"] = float(m.group(1))

        # 运动粘度: "粘度1e-6" 或 "viscosity 1e-6"
        m = re.search(r"粘度\s*([0-9.eE-]+)", full_text)
        if m and "kinematic_viscosity" not in params:
            params["kinematic_viscosity"] = float(m.group(1))

        # 从 material_facts 提取
        if physics_spec and physics_spec.material_facts:
            mf = physics_spec.material_facts
            if "density" in mf and "density" not in params:
                params["density"] = _to_float(mf["density"])
            if "kinematic_viscosity" in mf and "kinematic_viscosity" not in params:
                params["kinematic_viscosity"] = _to_float(mf["kinematic_viscosity"])

        # 从 geometry_facts 提取
        if physics_spec and physics_spec.geometry_facts:
            gf = physics_spec.geometry_facts
            if "diameter" in gf and "diameter" not in params:
                params["diameter"] = _to_float(gf["diameter"])
            if "length" in gf and "length" not in params:
                params["length"] = _to_float(gf["length"])

        # 从 operating_conditions 提取
        if physics_spec and physics_spec.operating_conditions:
            oc = physics_spec.operating_conditions
            if "inlet_velocity" in oc and "inlet_velocity" not in params:
                params["inlet_velocity"] = _to_float(oc["inlet_velocity"])
            if "mean_velocity" in oc and "mean_velocity" not in params:
                params["mean_velocity"] = _to_float(oc["mean_velocity"])

        # 清除 None 值
        return {k: v for k, v in params.items() if v is not None}

    @staticmethod
    def _convert_physics_spec(
        research_physics: ResearchPhysicsSpec | None,
    ) -> PhysicsSpec:
        """将 ResearchPhysicsSpec 转换为 experiment_spec.PhysicsSpec。

        当 ResearchPhysicsSpec 的字段为 None 时，PhysicsSpec 的对应字段
        也保持 None（未知），不再静默填充硬编码默认值。
        """
        if research_physics is None:
            return PhysicsSpec()  # 所有高风险字段为 None（未知）

        # 安全地转换字符串到枚举，None 保留为 None
        def safe_enum(enum_cls, value):
            if value is None:
                return None
            try:
                return enum_cls(value)
            except (ValueError, KeyError):
                return None

        return PhysicsSpec(
            dimensions=safe_enum(Dimensions, research_physics.dimensions),
            phases=safe_enum(PhaseType, research_physics.phases),
            compressibility=safe_enum(Compressibility, research_physics.compressibility),
            flow_regime=safe_enum(FlowRegime, research_physics.flow_regime),
            temporal_type=safe_enum(TemporalType, research_physics.temporal_type),
            gravity_enabled=None,
        )

    @staticmethod
    def _map_task_type(task_type_str: str) -> TaskType:
        """将意图评估的 task_type 映射到 ExperimentSpec 的 TaskType。"""
        mapping = {
            "new_simulation": TaskType.NEW_SIMULATION,
            "parameter_sensitivity": TaskType.PARAMETER_SENSITIVITY,
            "mechanism_analysis": TaskType.MECHANISM_ANALYSIS,
            "engineering_prediction": TaskType.ENGINEERING_PREDICTION,
            "paper_reproduction": TaskType.PAPER_REPRODUCTION,
            "benchmark_reproduction": TaskType.BENCHMARK_REPRODUCTION,
            "model_comparison": TaskType.MODEL_COMPARISON,
            "case_diagnosis": TaskType.CASE_DIAGNOSIS,
        }
        return mapping.get(task_type_str, TaskType.NEW_SIMULATION)


__all__ = ["ExperimentSpecFactory"]
