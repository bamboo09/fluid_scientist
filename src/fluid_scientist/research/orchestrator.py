"""研究编排器：研究需求的唯一入口。

协调 IntentEngine、ScopeEngine 和 SessionStore，管理多轮澄清流程。
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from fluid_scientist.compat import UTC
from fluid_scientist.research.intent_engine import IntentEngine
from fluid_scientist.research.models import (
    ClarificationRequired,
    ClarificationTurn,
    DraftReady,
    ExtractedFact,
    IntentAssessment,
    ResearchPhysicsSpec,
    ResearchSession,
    ResearchSessionStatus,
    ResearchTurnResult,
    UnsupportedRequest,
)
from fluid_scientist.research.scope_engine import ScopeEngine
from fluid_scientist.research.session_store import SessionStore
from fluid_scientist.research.spec_factory import ExperimentSpecFactory

if TYPE_CHECKING:
    from fluid_scientist.measurement.planner import MetricPlanner


# physical_system (IntentAssessment) → registry experiment_type
_PHYSICAL_SYSTEM_TO_EXPERIMENT_TYPE: dict[str, str] = {
    "internal_flow": "laminar_pipe",
    "external_flow": "cylinder_flow",
    "cavity_flow": "lid_driven_cavity",
}


class ResearchOrchestrator:
    """研究需求编排器，管理从需求收集到草稿生成的完整流程。"""

    def __init__(
        self,
        session_store: SessionStore,
        intent_engine: IntentEngine,
        scope_engine: ScopeEngine,
        spec_factory: ExperimentSpecFactory | None = None,
        workflow_repository=None,  # WorkflowRepository Protocol, optional
        metric_planner: MetricPlanner | None = None,
    ) -> None:
        """初始化编排器。

        Args:
            session_store: 会话存储。
            intent_engine: 意图引擎。
            scope_engine: 范围引擎。
            spec_factory: ExperimentSpec 工厂，可选。
            workflow_repository: 工作流仓库，可选。
            metric_planner: 指标计划器，可选。当提供时，在生成 ExperimentSpec
                后会调用它生成 MeasurementPlan 并附加到 spec.metrics 中。
        """
        self._store = session_store
        self._intent_engine = intent_engine
        self._scope_engine = scope_engine
        self._spec_factory = spec_factory
        self._repository = workflow_repository
        self._metric_planner = metric_planner

    def start_session(
        self,
        project_id: str,
        message: str,
    ) -> ResearchTurnResult:
        """创建新研究会话并处理第一轮。

        Args:
            project_id: 所属项目 ID。
            message: 用户的初始请求消息。

        Returns:
            第一轮处理结果。
        """
        now = datetime.now(UTC).isoformat()
        session_id = uuid4().hex[:12]
        session = ResearchSession(
            session_id=session_id,
            project_id=project_id,
            status=ResearchSessionStatus.COLLECTING_REQUIREMENTS,
            original_request=message,
            accumulated_context={},
            confirmed_facts=[],
            assumptions=[],
            unresolved_questions=[],
            turns=[],
            created_at=now,
            updated_at=now,
        )
        self._store.create(session)
        return self._process_turn(session, message)

    def handle_turn(
        self,
        session_id: str,
        user_message: str,
    ) -> ResearchTurnResult:
        """处理后续轮次。

        Args:
            session_id: 已有会话的 ID。
            user_message: 用户的新消息。

        Returns:
            本轮处理结果。

        Raises:
            KeyError: 如果会话不存在。
        """
        session = self._store.get(session_id)
        return self._process_turn(session, user_message)

    def _process_turn(
        self,
        session: ResearchSession,
        user_message: str,
    ) -> ResearchTurnResult:
        """处理单轮对话的核心逻辑。"""

        now = datetime.now(UTC).isoformat()
        turn_id = uuid4().hex[:12]

        # 1. 记录澄清轮次
        turn = ClarificationTurn(
            turn_id=turn_id,
            session_id=session.session_id,
            user_message=user_message,
            assistant_questions=[],
            extracted_facts=[],
            created_at=now,
        )

        # 2. 调用意图引擎评估意图
        intent = self._intent_engine.assess_intent(
            user_message=user_message,
            accumulated_context=session.accumulated_context,
            confirmed_facts=session.confirmed_facts,
        )

        # 3. 从本轮消息中提取事实
        extracted_facts = self._extract_facts(user_message, intent)
        turn = turn.model_copy(update={"extracted_facts": extracted_facts})

        # 4. 更新会话的累积事实和意图评估（按 category+key 去重，保留最新）
        all_facts = self._merge_facts(session.confirmed_facts, extracted_facts)
        physics_spec = self._build_physics_spec(session, all_facts)
        prev_messages = session.accumulated_context.get("all_messages", "")
        self._store.update(
            session.session_id,
            confirmed_facts=all_facts,
            intent_assessment=intent,
            physics_spec=physics_spec,
            turns=[*session.turns, turn],
            accumulated_context={
                **session.accumulated_context,
                "last_message": user_message,
                "turn_count": len(session.turns) + 1,
                "all_messages": f"{prev_messages} {user_message}".strip(),
                "research_objective": intent.research_objective,
            },
            updated_at=now,
        )
        updated_session = self._store.get(session.session_id)

        # 5. 不支持的请求
        if intent.unsupported_reason is not None:
            self._store.update(
                session.session_id,
                status=ResearchSessionStatus.UNSUPPORTED,
                updated_at=now,
            )
            return UnsupportedRequest(
                session_id=session.session_id,
                reason=intent.unsupported_reason,
                missing_capabilities=[],
            )

        # 6. 调用范围引擎评估是否需要澄清
        needs_clarification, questions = self._scope_engine.evaluate_scope(
            intent, updated_session
        )

        # 7. 需要澄清 → 返回 ClarificationRequired
        if needs_clarification or not intent.ready_for_draft:
            updated_turn = turn.model_copy(update={"assistant_questions": questions})
            self._store.update(
                session.session_id,
                status=ResearchSessionStatus.CLARIFICATION_REQUIRED,
                unresolved_questions=[q.text for q in questions],
                turns=[*session.turns[:-1], updated_turn],
                updated_at=datetime.now(UTC).isoformat(),
            )
            current_understanding = self._build_understanding(intent, updated_session)
            return ClarificationRequired(
                session_id=session.session_id,
                summary=self._build_summary(intent, questions),
                questions=questions,
                current_understanding=current_understanding,
            )

        # 8. ready_for_draft → 通过 Dynamic Schema 生成 ExperimentSpec
        experiment_spec_id = None
        warnings_list: list[str] = []

        if self._spec_factory is not None and self._repository is not None:
            try:
                updated_session = self._store.get(session.session_id)
                spec = self._spec_factory.create_from_schema(
                    session=updated_session,
                    intent=intent,
                    physics_spec=updated_session.physics_spec,
                )

                # 8a. 调用 MetricPlanner 生成 MeasurementPlan 并附加到 spec.metrics
                spec = self._attach_measurement_plan(spec, intent, updated_session)

                # 存储到 workflow repository
                from fluid_scientist.ports import StoredExperimentSpec
                stored_spec = StoredExperimentSpec(
                    experiment_id=spec.experiment_id,
                    project_id=session.project_id,
                    schema_version=spec.schema_version,
                    experiment_version=spec.experiment_version,
                    status=spec.status.value,
                    task_type=spec.task_type.value,
                    interaction_mode=spec.interaction_mode.value,
                    spec_json=spec.model_dump_json(),
                    created_at=now,
                    updated_at=now,
                )
                self._repository.save_experiment_spec(stored_spec)
                experiment_spec_id = spec.experiment_id

                # 更新会话状态
                self._store.update(
                    session.session_id,
                    status=ResearchSessionStatus.DRAFT_READY,
                    experiment_spec_id=experiment_spec_id,
                    unresolved_questions=[],
                    updated_at=now,
                )
            except Exception as e:
                warnings_list.append(f"ExperimentSpec 生成失败: {e}")
                self._store.update(
                    session.session_id,
                    status=ResearchSessionStatus.DRAFT_READY,
                    unresolved_questions=[],
                    updated_at=now,
                )
        else:
            self._store.update(
                session.session_id,
                status=ResearchSessionStatus.DRAFT_READY,
                unresolved_questions=[],
                updated_at=now,
            )

        return DraftReady(
            session_id=session.session_id,
            experiment_spec_id=experiment_spec_id,
            experiment_version=1,
            warnings=warnings_list,
        )

    def _attach_measurement_plan(
        self,
        spec: Any,
        intent: IntentAssessment,
        session: ResearchSession,
    ) -> Any:
        """调用 MetricPlanner 生成 MeasurementPlan 并附加到 spec.metrics。

        如果未配置 metric_planner，则原样返回 spec。
        """
        if self._metric_planner is None:
            return spec

        try:
            experiment_type = _PHYSICAL_SYSTEM_TO_EXPERIMENT_TYPE.get(
                intent.physical_system or "", "unknown"
            )
            metric_plan = self._metric_planner.propose_metrics(
                research_objective=intent.research_objective or "",
                physics_spec=session.physics_spec,
                user_metrics=list(intent.requested_metrics),
                experiment_type=experiment_type,
            )
            measurement_plan_dict = metric_plan.measurement_plan.model_dump()
            new_metrics = list(spec.metrics) + [
                {
                    "kind": "measurement_plan",
                    "measurement_plan": measurement_plan_dict,
                    "core_metrics": metric_plan.core_metrics,
                    "credibility_metrics": metric_plan.credibility_metrics,
                    "extension_metrics": metric_plan.extension_metrics,
                    "unknown_metrics": metric_plan.unknown_metrics,
                }
            ]
            return spec.model_copy(update={"metrics": new_metrics})
        except Exception:
            # MetricPlanner 失败不应阻塞草稿生成
            return spec

    def _extract_facts(
        self,
        user_message: str,
        intent: IntentAssessment,
    ) -> list[ExtractedFact]:
        """从用户消息和意图评估中提取结构化事实。"""
        facts: list[ExtractedFact] = []
        message_lower = user_message.lower()

        # 物理系统
        if intent.physical_system is not None:
            facts.append(
                ExtractedFact(
                    fact_id=uuid4().hex[:12],
                    category="geometry",
                    key="physical_system",
                    value=intent.physical_system,
                    confidence=0.8,
                    source="user_input",
                )
            )

        # 流态
        if any(kw in message_lower for kw in ("层流", "laminar")):
            facts.append(
                ExtractedFact(
                    fact_id=uuid4().hex[:12],
                    category="operating_condition",
                    key="flow_regime",
                    value="laminar",
                    confidence=0.9,
                )
            )
        elif any(kw in message_lower for kw in ("湍流", "turbulent")):
            facts.append(
                ExtractedFact(
                    fact_id=uuid4().hex[:12],
                    category="operating_condition",
                    key="flow_regime",
                    value="turbulent",
                    confidence=0.9,
                )
            )

        # 流体介质
        if any(kw in message_lower for kw in ("水", "water")):
            facts.append(
                ExtractedFact(
                    fact_id=uuid4().hex[:12],
                    category="material",
                    key="fluid_type",
                    value="water",
                    confidence=0.9,
                )
            )
        elif any(kw in message_lower for kw in ("空气", "air")):
            facts.append(
                ExtractedFact(
                    fact_id=uuid4().hex[:12],
                    category="material",
                    key="fluid_type",
                    value="air",
                    confidence=0.9,
                )
            )

        # 请求的指标
        for metric in intent.requested_metrics:
            facts.append(
                ExtractedFact(
                    fact_id=uuid4().hex[:12],
                    category="metric",
                    key=metric,
                    value="requested",
                    confidence=0.8,
                )
            )

        # 目标现象
        for phenomenon in intent.target_phenomena:
            facts.append(
                ExtractedFact(
                    fact_id=uuid4().hex[:12],
                    category="objective",
                    key="target_phenomenon",
                    value=phenomenon,
                    confidence=0.8,
                )
            )

        return facts

    @staticmethod
    def _merge_facts(
        existing: list[ExtractedFact],
        new_facts: list[ExtractedFact],
    ) -> list[ExtractedFact]:
        """合并事实列表，按 (category, key) 去重，保留最新值。"""
        merged: dict[tuple[str, str], ExtractedFact] = {}
        for fact in existing:
            merged[(fact.category, fact.key)] = fact
        for fact in new_facts:
            merged[(fact.category, fact.key)] = fact
        return list(merged.values())

    @staticmethod
    def _build_physics_spec(
        session: ResearchSession,
        all_facts: list[ExtractedFact],
    ) -> ResearchPhysicsSpec:
        """从累积事实构建物理规格。"""
        # 以已有 physics_spec 为基础
        existing = session.physics_spec
        flow_regime = existing.flow_regime if existing else None
        material_facts: dict[str, Any] = (
            dict(existing.material_facts) if existing else {}
        )
        geometry_facts: dict[str, Any] = (
            dict(existing.geometry_facts) if existing else {}
        )
        operating_conditions: dict[str, Any] = (
            dict(existing.operating_conditions) if existing else {}
        )

        for fact in all_facts:
            if fact.category == "operating_condition" and fact.key == "flow_regime":
                flow_regime = fact.value
            elif fact.category == "material":
                material_facts[fact.key] = fact.value
            elif fact.category == "geometry":
                geometry_facts[fact.key] = fact.value
            elif fact.category == "operating_condition":
                operating_conditions[fact.key] = fact.value

        return ResearchPhysicsSpec(
            flow_regime=flow_regime,
            geometry_facts=geometry_facts,
            material_facts=material_facts,
            operating_conditions=operating_conditions,
        )

    @staticmethod
    def _build_understanding(
        intent: IntentAssessment,
        session: ResearchSession,
    ) -> dict[str, Any]:
        """构建当前理解摘要，供前端展示。"""
        return {
            "physical_system": intent.physical_system,
            "research_objective": intent.research_objective,
            "requested_metrics": intent.requested_metrics,
            "target_phenomena": intent.target_phenomena,
            "confirmed_facts_count": len(session.confirmed_facts),
            "turn_count": len(session.turns),
        }

    @staticmethod
    def _build_summary(
        intent: IntentAssessment,
        questions: list,
    ) -> str:
        """构建澄清摘要文本。"""
        parts: list[str] = []
        if intent.physical_system:
            parts.append(f"已识别物理系统: {intent.physical_system}")
        else:
            parts.append("尚未确定物理系统")
        if intent.requested_metrics:
            parts.append(f"关注指标: {', '.join(intent.requested_metrics)}")
        parts.append(f"需要澄清 {len(questions)} 个问题")
        return "；".join(parts)


__all__ = ["ResearchOrchestrator"]
