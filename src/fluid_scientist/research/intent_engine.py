"""意图引擎：从用户输入评估研究意图。

支持两种模式：
- fake 模式（无 LLM）：基于关键词的规则匹配
- real 模式（有 LLM provider）：调用 LLM 进行结构化意图评估

在 Commit 1 中仅实现 fake 模式，real 模式留 TODO 标记。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fluid_scientist.research.models import ExtractedFact, IntentAssessment

if TYPE_CHECKING:
    from fluid_scientist.experiment_planning.providers import ExperimentDesigner

# 不支持的物理类型关键词
_UNSUPPORTED_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("多相流", "multiphase"),
    ("燃烧", "combustion"),
    ("传热", "heat transfer"),
    ("可压缩", "compressible flow"),
)


class IntentEngine:
    """意图评估引擎，从用户消息中提取研究意图和物理信息。"""

    def __init__(
        self,
        plan_designer: ExperimentDesigner | None = None,
        provider_name: str | None = None,
        model_name: str | None = None,
    ) -> None:
        """初始化意图引擎。

        Args:
            plan_designer: 现有的 ExperimentDesigner Protocol 实例，
                在 fake 模式下为 None。
            provider_name: LLM provider 名称（如 "openai"、"glm"）。
            model_name: LLM 模型名称。
        """
        self._plan_designer = plan_designer
        self._provider_name = provider_name
        self._model_name = model_name

    @property
    def is_real_mode(self) -> bool:
        """是否处于 real 模式（有 LLM provider）。"""
        return self._plan_designer is not None

    def assess_intent(
        self,
        user_message: str,
        accumulated_context: dict[str, Any],
        confirmed_facts: list[ExtractedFact],
    ) -> IntentAssessment:
        """评估用户消息中的研究意图。

        Args:
            user_message: 用户输入的消息文本。
            accumulated_context: 累积的上下文信息，包含之前所有轮次的消息。
            confirmed_facts: 已确认的事实列表。

        Returns:
            意图评估结果。
        """
        if self.is_real_mode:
            # TODO(Commit 2+): 实现 real 模式的 LLM 调用
            # 构造 system prompt 让 LLM 返回 JSON 格式的 IntentAssessment
            # 如果 LLM 调用失败，fallback 到 fake 模式
            return self._assess_intent_fake(
                user_message, accumulated_context, confirmed_facts
            )
        return self._assess_intent_fake(user_message, accumulated_context, confirmed_facts)

    def _assess_intent_fake(
        self,
        user_message: str,
        accumulated_context: dict[str, Any],
        confirmed_facts: list[ExtractedFact],
    ) -> IntentAssessment:
        """fake 模式：基于关键词规则匹配评估意图。"""

        # 合并所有历史消息和当前消息，用于关键词检测
        all_messages = accumulated_context.get("all_messages", "")
        combined_text = f"{all_messages} {user_message}"
        combined_lower = combined_text.lower()

        # --- 不支持类型检测 ---
        for cn_kw, en_kw in _UNSUPPORTED_KEYWORDS:
            if cn_kw in combined_lower or en_kw in combined_lower:
                return IntentAssessment(
                    task_type="new_simulation",
                    research_objective=None,
                    physical_system=None,
                    confidence=0.0,
                    ready_for_draft=False,
                    unsupported_reason=f"当前系统暂不支持{cn_kw}相关模拟",
                )

        physical_system: str | None = None
        target_phenomena: list[str] = []
        requested_metrics: list[str] = []

        # --- 物理系统识别（从消息文本） ---
        if any(kw in combined_lower for kw in ("弯管", "pipe", "管")):
            physical_system = "internal_flow"
        if any(kw in combined_lower for kw in ("圆柱", "cylinder")):
            physical_system = "external_flow"
        if any(kw in combined_lower for kw in ("方腔", "cavity")):
            physical_system = "cavity_flow"

        # --- 物理系统识别（从已确认事实回退） ---
        if physical_system is None:
            for fact in confirmed_facts:
                if fact.category == "geometry" and fact.key == "physical_system":
                    physical_system = fact.value
                    break

        # --- 指标识别 ---
        if any(kw in combined_lower for kw in ("压降", "pressure drop")):
            requested_metrics.append("pressure_drop")
        if any(kw in combined_lower for kw in ("阻力", "drag")):
            requested_metrics.append("drag_coefficient")
        if any(kw in combined_lower for kw in ("速度", "velocity")):
            requested_metrics.append("velocity_profile")

        # --- 指标识别（从已确认事实回退） ---
        for fact in confirmed_facts:
            if (
                fact.category == "metric"
                and fact.value == "requested"
                and fact.key not in requested_metrics
            ):
                requested_metrics.append(fact.key)

        # --- 目标现象识别 ---
        if any(kw in combined_lower for kw in ("二次流", "secondary flow")):
            target_phenomena.append("secondary_flow")

        # --- 流态关键词检测（用于置信度计算） ---
        has_flow_regime_keyword = any(
            kw in combined_lower for kw in ("层流", "laminar", "湍流", "turbulent")
        )

        # --- 研究目标推断 ---
        # 优先使用已有 research_objective（从 accumulated_context）
        previous_objective = accumulated_context.get("research_objective")
        research_objective = self._infer_research_objective(
            user_message, physical_system, requested_metrics, target_phenomena
        )
        if research_objective is None and previous_objective is not None:
            research_objective = previous_objective

        # --- ready_for_draft 判定 ---
        missing_info: list[str] = []
        if physical_system is None:
            missing_info.append("physical_system")
        if research_objective is None or len(research_objective) < 10:
            missing_info.append("research_objective")
        if len(user_message) < 15 and all_messages == "":
            missing_info.append("request_too_short")
        if not requested_metrics:
            missing_info.append("requested_metrics")

        ready_for_draft = len(missing_info) == 0

        confidence = 0.0
        if physical_system is not None:
            confidence += 0.3
        if research_objective is not None and len(research_objective) >= 10:
            confidence += 0.3
        if requested_metrics:
            confidence += 0.2
        if has_flow_regime_keyword:
            confidence += 0.2

        return IntentAssessment(
            task_type="new_simulation",
            research_objective=research_objective,
            physical_system=physical_system,
            target_phenomena=target_phenomena,
            comparison_dimensions=[],
            requested_metrics=requested_metrics,
            confidence=confidence,
            missing_critical_information=missing_info,
            ready_for_draft=ready_for_draft,
            unsupported_reason=None,
        )

    @staticmethod
    def _infer_research_objective(
        user_message: str,
        physical_system: str | None,
        requested_metrics: list[str],
        target_phenomena: list[str],
    ) -> str | None:
        """根据已知信息推断研究目标描述。"""
        if len(user_message) < 15:
            return None

        parts: list[str] = []
        if physical_system == "internal_flow":
            parts.append("管内流动")
        elif physical_system == "external_flow":
            parts.append("外部绕流")
        elif physical_system == "cavity_flow":
            parts.append("方腔流动")

        if target_phenomena:
            parts.append("、".join(target_phenomena))
        if requested_metrics:
            parts.append("关注" + "、".join(requested_metrics))

        if not parts:
            return user_message if len(user_message) >= 10 else None

        if len(parts) > 1:
            return "研究" + "的".join(parts)
        return f"研究{parts[0]}"


__all__ = ["IntentEngine"]
