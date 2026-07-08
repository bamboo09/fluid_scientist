"""Workbench agent — processes natural language workbench edits.

Supports two modes:
- Real mode: Uses LLM with workbench_edit_prompt
- Fake mode: Rule-based intent detection for testing without LLM

The agent generates an EditProposal (NOT direct modifications). The user
confirms the proposal, then SpecEditExecutor applies it deterministically.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import uuid4

from fluid_scientist.prompts import load_prompt
from fluid_scientist.workbench.edit_models import (
    EditProposal,
    ProposedMetric,
    ProposedParameter,
    SpecEditOperation,
)

logger = logging.getLogger(__name__)

# Material database for fluid changes
_FLUID_PROPS: dict[str, dict[str, Any]] = {
    "air": {"density": 1.225, "kinematic_viscosity": 1.5e-5},
    "water": {"density": 998.2, "kinematic_viscosity": 1e-6},
}


class WorkbenchAgent:
    """Agent that processes natural language workbench edits.

    Supports two modes:
    - Real mode: Uses LLM with workbench_edit_prompt
    - Fake mode: Rule-based intent detection
    """

    def __init__(
        self,
        llm_client: Any | None = None,
        model_name: str = "gpt-4",
        provider: str = "openai",
    ) -> None:
        self.llm_client = llm_client
        self.model_name = model_name
        self.provider = provider
        self._fake_mode = llm_client is None

    def process_turn(
        self,
        user_message: str,
        spec: dict,
        validation_state: dict | None = None,
    ) -> EditProposal:
        """Process a workbench turn and return EditProposal.

        Args:
            user_message: The user's natural language message.
            spec: The current ExperimentSpec as a dict.
            validation_state: Optional validation state dict.

        Returns:
            An EditProposal for user confirmation.
        """
        if self._fake_mode:
            return self._process_turn_fake(
                user_message, spec, validation_state
            )
        return self._process_turn_llm(user_message, spec, validation_state)

    # ------------------------------------------------------------------
    # Fake mode: rule-based intent detection
    # ------------------------------------------------------------------

    def _process_turn_fake(
        self,
        user_message: str,
        spec: dict,
        validation_state: dict | None,
    ) -> EditProposal:
        """Process a workbench turn using rule-based intent detection."""
        msg = user_message.strip()
        msg_lower = msg.lower()
        experiment_id = spec.get("experiment_id", "unknown")
        experiment_version = spec.get("experiment_version", 1)

        # 1. Add parameter without name → clarification
        if "增加一个参数" in msg or "添加一个参数" in msg:
            return self._make_proposal(
                experiment_id=experiment_id,
                experiment_version=experiment_version,
                edit_intent="clarification_required",
                summary="用户想增加参数但未指定名称",
                clarification_question="请问要增加什么参数？请提供参数名称或描述。",
                requires_confirmation=False,
            )

        # 2. Add wall_roughness
        if "壁面粗糙度" in msg or "wall_roughness" in msg_lower:
            return self._make_proposal(
                experiment_id=experiment_id,
                experiment_version=experiment_version,
                edit_intent="add_parameter",
                summary="增加壁面粗糙度参数",
                proposed_operations=[
                    SpecEditOperation(
                        operation="add_parameter",
                        target_id="wall_roughness",
                        parameter=ProposedParameter(
                            parameter_id="wall_roughness",
                            display_name="壁面粗糙度",
                            category="material_property",
                            unit="m",
                            value=0,
                            status="system_recommended",
                            source="system_recommended",
                            reason="壁面粗糙度参数",
                            criticality="low",
                        ),
                        reason="用户要求增加壁面粗糙度参数",
                    ),
                ],
                invalidates=["compiled_case"],
            )

        # 3. Add lift coefficient metric
        if "升力" in msg or "lift" in msg_lower:
            return self._make_proposal(
                experiment_id=experiment_id,
                experiment_version=experiment_version,
                edit_intent="add_metric",
                summary="增加升力系数指标",
                proposed_operations=[
                    SpecEditOperation(
                        operation="add_metric",
                        target_id="lift_coefficient",
                        metric=ProposedMetric(
                            metric_id="lift_coefficient",
                            display_name="升力系数 Cl",
                            definition="Fl / (0.5 * rho * U^2 * A)",
                            required_data=["forceCoeffs"],
                            measurement_requirements=["forceCoeffs"],
                            quality_checks=[
                                "statistical_convergence",
                                "minimum_cycles",
                            ],
                            reason="用户要求增加升力系数指标",
                        ),
                        reason="用户要求增加升力系数指标",
                    ),
                ],
                invalidates=["measurement_plan"],
            )

        # 4. Change fluid to air
        if "流体改为空气" in msg or "改为空气" in msg or (
            "空气" in msg and "改" in msg
        ):
            return self._make_fluid_change_proposal(
                experiment_id=experiment_id,
                experiment_version=experiment_version,
                fluid_name="air",
                fluid_label="空气",
            )

        # 5. Change fluid to water
        if "流体改为水" in msg or "改为水" in msg or (
            "水" in msg and "改" in msg and "改为" in msg
        ):
            return self._make_fluid_change_proposal(
                experiment_id=experiment_id,
                experiment_version=experiment_version,
                fluid_name="water",
                fluid_label="水",
            )

        # 6. Parameter value changes — use nl_parser
        nl_result = self._try_nl_parse(msg, spec)
        if nl_result is not None:
            return nl_result

        # 7. Accept recommendations
        if "接受推荐" in msg or "接受所有推荐" in msg or "接受所有推荐值" in msg:
            return self._make_proposal(
                experiment_id=experiment_id,
                experiment_version=experiment_version,
                edit_intent="accept_recommendations",
                summary="接受所有系统推荐值",
                proposed_operations=[
                    SpecEditOperation(
                        operation="accept_recommendation",
                        target_id=None,
                        reason="接受所有 system_recommended 参数",
                    ),
                ],
                requires_confirmation=True,
            )

        # 8. Validate / check
        if "验证" in msg or "检查" in msg:
            return self._make_proposal(
                experiment_id=experiment_id,
                experiment_version=experiment_version,
                edit_intent="validate_spec",
                summary="验证当前实验规格",
                requires_confirmation=False,
            )

        # 9. Prepare compile
        if "编译" in msg or "准备编译" in msg:
            return self._make_proposal(
                experiment_id=experiment_id,
                experiment_version=experiment_version,
                edit_intent="prepare_compile",
                summary="准备编译实验",
                requires_confirmation=True,
            )

        # 10. Default → clarification
        return self._make_proposal(
            experiment_id=experiment_id,
            experiment_version=experiment_version,
            edit_intent="clarification_required",
            summary="无法理解用户的修改意图",
            clarification_question=(
                "我没有理解您的需求。您可以尝试：\n"
                "- 修改参数值（如：把管径改成50毫米）\n"
                "- 增加参数（如：增加壁面粗糙度）\n"
                "- 增加指标（如：还想看升力系数）\n"
                "- 更换流体（如：把流体改为空气）\n"
                "- 接受推荐值（如：接受所有推荐）"
            ),
            requires_confirmation=False,
        )

    def _try_nl_parse(
        self, msg: str, spec: dict
    ) -> EditProposal | None:
        """Try to parse parameter value changes using nl_parser.

        Returns an EditProposal if parameter changes were detected,
        or None if no parameter changes were found.
        """
        try:
            from fluid_scientist.experiment_spec.models import (
                ExperimentSpec,
            )
            from fluid_scientist.experiment_spec.nl_parser import (
                parse_nl_instruction,
            )

            spec_obj = ExperimentSpec.model_validate(spec)
            result = parse_nl_instruction(msg, spec_obj)
        except Exception:
            return None

        if not result.proposed_changes:
            return None

        operations: list[SpecEditOperation] = []
        for change in result.proposed_changes:
            operations.append(
                SpecEditOperation(
                    operation="update_parameter",
                    target_id=change.parameter_id,
                    value=change.new_value,
                    unit=change.unit,
                    reason=f"用户修改{change.display_name}",
                )
            )

        return self._make_proposal(
            experiment_id=spec.get("experiment_id", "unknown"),
            experiment_version=spec.get("experiment_version", 1),
            edit_intent="update_parameter",
            summary=f"修改{len(operations)}个参数",
            proposed_operations=operations,
            invalidates=["compiled_case"],
        )

    def _make_fluid_change_proposal(
        self,
        experiment_id: str,
        experiment_version: int,
        fluid_name: str,
        fluid_label: str,
    ) -> EditProposal:
        """Create a proposal for changing the fluid type."""
        props = _FLUID_PROPS.get(fluid_name, {})
        operations: list[SpecEditOperation] = []

        if "density" in props:
            operations.append(
                SpecEditOperation(
                    operation="update_parameter",
                    target_id="density",
                    value=props["density"],
                    unit="kg/m3",
                    reason=f"流体改为{fluid_label}，密度更新",
                )
            )
        if "kinematic_viscosity" in props:
            operations.append(
                SpecEditOperation(
                    operation="update_parameter",
                    target_id="kinematic_viscosity",
                    value=props["kinematic_viscosity"],
                    unit="m^2/s",
                    reason=f"流体改为{fluid_label}，运动粘度更新",
                )
            )

        return self._make_proposal(
            experiment_id=experiment_id,
            experiment_version=experiment_version,
            edit_intent="change_physics_model",
            summary=f"把流体改为{fluid_label}",
            proposed_operations=operations,
            invalidates=["measurement_plan", "compiled_case"],
        )

    @staticmethod
    def _make_proposal(
        experiment_id: str,
        experiment_version: int,
        edit_intent: str,
        summary: str = "",
        proposed_operations: list[SpecEditOperation] | None = None,
        clarification_question: str | None = None,
        requires_confirmation: bool = True,
        invalidates: list[str] | None = None,
    ) -> EditProposal:
        """Build an EditProposal with a generated proposal_id."""
        return EditProposal(
            proposal_id=f"prop-{uuid4().hex[:16]}",
            experiment_id=experiment_id,
            experiment_version=experiment_version,
            edit_intent=edit_intent,
            summary=summary,
            proposed_operations=proposed_operations or [],
            clarification_question=clarification_question,
            requires_confirmation=requires_confirmation,
            invalidates=invalidates or [],
        )

    # ------------------------------------------------------------------
    # LLM mode
    # ------------------------------------------------------------------

    def _process_turn_llm(
        self,
        user_message: str,
        spec: dict,
        validation_state: dict | None,
    ) -> EditProposal:
        """Process a workbench turn using LLM."""
        system_prompt = load_prompt("workbench_edit")

        # Build the user message with spec context
        spec_context = json.dumps(spec, ensure_ascii=False, default=str)
        context_parts = [
            f"[当前 ExperimentSpec]\n{spec_context}",
        ]
        if validation_state:
            context_parts.append(
                f"[验证状态]\n{json.dumps(validation_state, ensure_ascii=False)}"
            )
        context_parts.append(f"[用户消息]\n{user_message}")
        augmented_content = "\n\n".join(context_parts)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": augmented_content},
        ]

        try:
            response = self.llm_client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content
            data = json.loads(content)
        except Exception as e:
            logger.warning("LLM call failed, falling back to fake mode: %s", e)
            return self._process_turn_fake(
                user_message, spec, validation_state
            )

        return self._llm_response_to_proposal(data, spec)

    @staticmethod
    def _llm_response_to_proposal(
        data: dict, spec: dict
    ) -> EditProposal:
        """Convert an LLM JSON response to an EditProposal."""
        operations: list[SpecEditOperation] = []
        for op_data in data.get("proposed_operations", []):
            param_data = op_data.get("parameter")
            metric_data = op_data.get("metric")
            operations.append(
                SpecEditOperation(
                    operation=op_data.get("operation", "update_parameter"),
                    target_id=op_data.get("target_id"),
                    parameter=(
                        ProposedParameter(**param_data)
                        if param_data
                        else None
                    ),
                    metric=(
                        ProposedMetric(**metric_data)
                        if metric_data
                        else None
                    ),
                    value=op_data.get("value"),
                    unit=op_data.get("unit"),
                    reason=op_data.get("reason", ""),
                )
            )

        return EditProposal(
            proposal_id=f"prop-{uuid4().hex[:16]}",
            experiment_id=spec.get("experiment_id", "unknown"),
            experiment_version=spec.get("experiment_version", 1),
            edit_intent=data.get("edit_intent", "clarification_required"),
            summary=data.get("summary", ""),
            proposed_operations=operations,
            clarification_question=data.get("clarification_question"),
            requires_confirmation=data.get("requires_confirmation", True),
            blocking_issues_preview=data.get(
                "blocking_issues_preview", []
            ),
            warnings_preview=data.get("warnings_preview", []),
            invalidates=data.get("invalidates", []),
        )


__all__ = ["WorkbenchAgent"]
