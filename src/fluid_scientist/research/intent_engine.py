"""意图引擎：从用户输入评估研究意图。

支持两种模式：
- fake 模式（无 LLM）：基于关键词的规则匹配
- real 模式（有 LLM client）：调用 LLM 进行结构化意图评估

在 real 模式下，如果 LLM 调用失败或响应校验失败，会自动回退到 fake 模式。
"""

from __future__ import annotations

import json
from typing import Any

from fluid_scientist.research.models import ExtractedFact, IntentAssessment

# 不支持的物理类型关键词
_UNSUPPORTED_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("多相流", "multiphase"),
    ("燃烧", "combustion"),
    ("传热", "heat transfer"),
    ("可压缩", "compressible flow"),
)

INTENT_SYSTEM_PROMPT = """You are a CFD research intent analyzer. \
Given a user's research request, extract structured intent information.

The user message may contain the following sections to provide multi-turn context:
- [对话历史]: Previous conversation messages accumulated across turns.
  Use this to understand the full context of the ongoing research discussion.
- [已确认事实]: Confirmed facts extracted from earlier turns, each with
  category, key, value, confidence, and turn_id. Treat these as established
  facts unless explicitly contradicted by the current user message.
- [当前用户消息]: The actual user message for the current turn. This is
  the primary input to analyze.

When the user message contains conversation history and confirmed facts,
use them to maintain consistency and avoid re-asking already-confirmed
information. The current user message takes precedence over historical
context if there is a conflict.

Return a JSON object with these fields:
- task_type: string
  (new_simulation, parameter_sensitivity, mechanism_analysis,
  benchmark_validation)
- research_objective: string
  (clear statement of what the user wants to investigate)
- physical_system: string or null
  (internal_flow, external_flow, cavity_flow, heat_transfer,
  multiphase, combustion)
- target_phenomena: array of strings
  (e.g. ["vortex shedding", "pressure drop", "secondary flow"])
- comparison_dimensions: array of strings
- explicitly_requested_metrics: array of strings
  (metrics the user explicitly mentioned)
- inferred_candidate_metrics: array of strings
  (metrics that would be relevant but weren't mentioned)
- confirmed_physics: object
  (physics parameters the user has confirmed, e.g.
  {"flow_regime": "laminar", "fluid": "water"})
- uncertain_physics: object
  (physics parameters that are uncertain, e.g.
  {"turbulence_model": "unknown"})
- critical_unknowns: array of objects with fields:
  field_id, reason, scientific_impact, options,
  recommended_option, recommendation_reason,
  require_explicit_confirmation
- assumptions: array of objects with fields:
  assumption_id, description, rationale, impact_level, field_id
- confidence: float (0-1)
- missing_critical_information: array of strings
- ready_for_draft: boolean
  (true if enough information to generate a draft ExperimentSpec)
- unsupported_reason: string or null

Rules:
1. If the request is too vague (e.g. just "研究弯管流动"),
   set ready_for_draft=false and list what's missing
   in missing_critical_information
2. High-risk physics (flow_regime, dimensions, compressibility,
   temporal_type) must be in confirmed_physics only if explicitly
   stated by the user; otherwise put in uncertain_physics
   or critical_unknowns
3. For each critical_unknown, provide a recommendation
   with reasoning
4. Do NOT silently default flow_regime to laminar
   or dimensions to 2D
5. Extract metrics from the user's description, even if they
   use non-standard names
6. Respond in the same language as the user's message
"""


class IntentEngine:
    """意图评估引擎，从用户消息中提取研究意图和物理信息。"""

    def __init__(
        self,
        llm_client: Any | None = None,
        model_name: str | None = None,
        provider_name: str | None = None,
        max_retries: int = 2,
    ) -> None:
        """初始化意图引擎。

        Args:
            llm_client: OpenAI 兼容的 LLM 客户端实例。如果为 None，则使用 fake 模式。
            model_name: LLM 模型名称。
            provider_name: LLM provider 名称（如 "openai"、"glm"、"deepseek"）。
            max_retries: LLM 响应校验失败时的最大重试次数。
        """
        self._llm_client = llm_client
        self._model_name = model_name
        self._provider_name = provider_name
        self._max_retries = max_retries

    @property
    def is_real_mode(self) -> bool:
        """是否处于 real 模式（有 LLM 客户端）。"""
        return self._llm_client is not None

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
            return self._assess_intent_with_llm(
                user_message, accumulated_context, confirmed_facts
            )
        result = self._assess_intent_fake(
            user_message, accumulated_context, confirmed_facts
        )
        result.fallback_reason = "No LLM client configured"
        return result

    def _assess_intent_with_llm(
        self,
        user_message: str,
        accumulated_context: dict[str, Any],
        confirmed_facts: list[ExtractedFact],
    ) -> IntentAssessment:
        """real 模式：调用 LLM 进行结构化意图评估。

        将多轮对话历史和已确认事实注入 LLM 消息，使 LLM 能够理解完整上下文。
        如果 LLM 调用失败或响应校验失败，自动回退到 fake 模式。
        """
        # Build context section
        context_parts: list[str] = []
        all_messages = accumulated_context.get("all_messages", "")
        if all_messages:
            context_parts.append(f"[对话历史]\n{all_messages}")

        if confirmed_facts:
            facts_text = "\n".join(
                f"- {f.category}/{f.key}: {f.value} "
                f"(confidence: {f.confidence}, turn: {f.turn_id})"
                for f in confirmed_facts
            )
            context_parts.append(f"[已确认事实]\n{facts_text}")

        context_section = "\n\n".join(context_parts)
        augmented_user_content = (
            f"{context_section}\n\n[当前用户消息]\n{user_message}"
            if context_section
            else user_message
        )

        messages = [
            {"role": "system", "content": INTENT_SYSTEM_PROMPT},
            {"role": "user", "content": augmented_user_content},
        ]

        last_error: Exception | None = None
        for _attempt in range(self._max_retries + 1):
            try:
                response = self._llm_client.chat.completions.create(
                    model=self._model_name,
                    messages=messages,
                    response_format={"type": "json_object"},
                )
            except Exception as e:
                # LLM 调用异常（网络错误等），立即回退
                result = self._assess_intent_fake(
                    user_message, accumulated_context, confirmed_facts
                )
                result.fallback_reason = f"LLM call failed: {e}"
                return result

            try:
                content = response.choices[0].message.content
                data = json.loads(content)
                assessment = IntentAssessment.model_validate(data)
                return assessment
            except Exception as e:
                last_error = e
                continue

        # 重试次数耗尽，回退到 fake 模式
        result = self._assess_intent_fake(
            user_message, accumulated_context, confirmed_facts
        )
        result.fallback_reason = f"LLM validation failed: {last_error}"
        return result

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
                    fallback_used=True,
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
        if any(kw in combined_lower for kw in ("阻升力", "升力", "lift", "频谱", "spectrum")):
            requested_metrics.append("force_spectrum")
        if any(kw in combined_lower for kw in ("尾迹偏斜", "wake deflection")):
            requested_metrics.append("wake_deflection")
        if any(kw in combined_lower for kw in ("展向翻转", "spanwise reversal")):
            requested_metrics.append("spanwise_reversal")
        if any(kw in combined_lower for kw in ("壁面涡", "涡结构", "wall vortex", "lambda2")):
            requested_metrics.append("wall_vortex_structure")

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
        if any(kw in combined_lower for kw in ("尾迹偏斜", "wake deflection")):
            target_phenomena.append("wake_deflection")
        if any(kw in combined_lower for kw in ("展向翻转", "spanwise reversal")):
            target_phenomena.append("spanwise_reversal")
        if any(kw in combined_lower for kw in ("壁面涡", "涡结构", "wall vortex", "lambda2")):
            target_phenomena.append("wall_vortex_structure")
        if any(kw in combined_lower for kw in ("阻升力", "升力", "频谱", "force spectrum")):
            target_phenomena.append("force_spectrum")

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
            fallback_used=True,
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
        if len(user_message) >= 20:
            return user_message

        if len(parts) > 1:
            return "研究" + "的".join(parts)
        return f"研究{parts[0]}"


__all__ = ["IntentEngine"]
