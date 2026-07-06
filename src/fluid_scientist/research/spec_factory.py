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
        2. 调用 generate_schema() 生成参数 schema
        3. 构造 ExperimentSpec
        """
        # 1. 转换 PhysicsSpec
        esp_physics = self._convert_physics_spec(physics_spec)

        # 2. 调用 Dynamic Schema Engine
        schema_result = generate_schema(esp_physics)

        # 3. 构造 ExperimentSpec
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
    def _convert_physics_spec(
        research_physics: ResearchPhysicsSpec | None,
    ) -> PhysicsSpec:
        """将 ResearchPhysicsSpec 转换为 experiment_spec.PhysicsSpec。"""
        if research_physics is None:
            return PhysicsSpec()  # 默认值

        # 安全地转换字符串到枚举
        def safe_enum(enum_cls, value, default):
            if value is None:
                return default
            try:
                return enum_cls(value)
            except (ValueError, KeyError):
                return default

        return PhysicsSpec(
            dimensions=safe_enum(Dimensions, research_physics.dimensions, Dimensions.TWO_D),
            phases=safe_enum(PhaseType, research_physics.phases, PhaseType.SINGLE_PHASE),
            compressibility=safe_enum(
                Compressibility, research_physics.compressibility, Compressibility.INCOMPRESSIBLE
            ),
            flow_regime=safe_enum(
                FlowRegime, research_physics.flow_regime, FlowRegime.LAMINAR
            ),
            temporal_type=safe_enum(
                TemporalType, research_physics.temporal_type, TemporalType.STEADY
            ),
            gravity_enabled=False,
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
