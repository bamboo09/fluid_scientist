"""范围引擎：判断是否需要澄清并生成高价值澄清问题。"""

from __future__ import annotations

from uuid import uuid4

from fluid_scientist.research.models import (
    ClarificationQuestion,
    IntentAssessment,
    ResearchSession,
)


class ScopeEngine:
    """范围评估引擎，判断研究需求是否充分，生成澄清问题。"""

    def evaluate_scope(
        self,
        intent: IntentAssessment,
        session: ResearchSession,
    ) -> tuple[bool, list[ClarificationQuestion]]:
        """评估研究需求的完整性，返回是否需要澄清及澄清问题列表。

        Args:
            intent: 当前意图评估结果。
            session: 当前研究会话。

        Returns:
            (needs_clarification, questions) 元组。
            needs_clarification 为 True 时 questions 非空。
        """
        questions: list[ClarificationQuestion] = []

        # 规则 1：物理系统未识别
        if intent.physical_system is None:
            questions.append(
                self._make_question(
                    "您主要研究哪种流动？管内流动、外部绕流还是方腔流动？",
                    options=["管内流动", "外部绕流", "方腔流动"],
                    rationale="需要确定物理系统类型才能选择合适的求解器配置。",
                )
            )

        # 规则 2：研究目标模糊（< 20 字）
        objective = intent.research_objective
        if objective is None or len(objective) < 20:
            questions.append(
                self._make_question(
                    "您主要关注什么物理现象？压降、阻力、速度分布、还是其他？",
                    options=["压降", "阻力", "速度分布", "其他"],
                    rationale="明确研究目标有助于聚焦关键物理量和后处理指标。",
                )
            )

        # 规则 3：没有请求任何指标
        if not intent.requested_metrics:
            questions.append(
                self._make_question(
                    "您希望获得哪些定量指标？",
                    options=["压降", "阻力系数", "速度剖面", "其他"],
                    rationale="定量指标决定后处理和实验设计方向。",
                )
            )

        # 规则 4：流态未确定
        flow_regime = self._get_flow_regime(session)
        if flow_regime is None:
            questions.append(
                self._make_question(
                    "流动是层流还是湍流？",
                    options=["层流", "湍流"],
                    rationale="流态直接影响求解器选择和湍流模型配置。",
                )
            )

        # 规则 5：没有材料信息（流体类型）
        if not self._has_material_facts(session):
            questions.append(
                self._make_question(
                    "流体介质是什么？水、空气还是其他？",
                    options=["水", "空气", "其他"],
                    rationale="流体介质决定物理属性（密度、粘度）的取值。",
                )
            )

        # 按优先级排序，最多返回 3 个问题
        needs_clarification = len(questions) > 0
        return needs_clarification, questions[:3]

    @staticmethod
    def _make_question(
        text: str,
        options: list[str],
        rationale: str | None = None,
    ) -> ClarificationQuestion:
        """创建一个澄清问题，自动生成 question_id。"""
        return ClarificationQuestion(
            question_id=uuid4().hex[:12],
            text=text,
            options=options[:4],
            allow_free_text=True,
            rationale=rationale,
        )

    @staticmethod
    def _get_flow_regime(session: ResearchSession) -> str | None:
        """从会话中获取流态信息。"""
        if session.physics_spec is not None and session.physics_spec.flow_regime:
            return session.physics_spec.flow_regime
        # 回退检查已确认事实
        for fact in session.confirmed_facts:
            if fact.category == "operating_condition" and fact.key == "flow_regime":
                return fact.value
        return None

    @staticmethod
    def _has_material_facts(session: ResearchSession) -> bool:
        """判断会话中是否已有材料信息。"""
        if session.physics_spec is not None and session.physics_spec.material_facts:
            return True
        # 检查已确认事实中是否有 material 类别
        return any(fact.category == "material" for fact in session.confirmed_facts)


__all__ = ["ScopeEngine"]
