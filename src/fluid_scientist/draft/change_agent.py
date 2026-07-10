"""Draft change proposal generation.

The :class:`DraftChangeAgent` parses a user's natural-language modification
request against a read-only :class:`ExperimentDraft` and produces a
:class:`ChangeProposal` — a structured set of edits that the user must
confirm before anything is applied.

This implementation is rule-based (no LLM) to keep the core deterministic.
The LLM prompt (``draft_change_prompt.txt``) is available for cases where
rule matching fails, but the agent always returns a structured proposal,
never a free-form response.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from fluid_scientist.draft.models import (
    ChangeProposal,
    DraftChange,
    DraftParameter,
    ExperimentDraft,
)

# Change types supported by the agent
CHANGE_TYPES = [
    "set_parameter",
    "add_parameter",
    "remove_parameter",
    "add_output",
    "remove_output",
    "change_boundary_condition",
    "change_initial_condition",
    "change_physics_model",
    "change_geometry",
    "change_mesh",
    "change_numerics",
    "change_solver",
    "add_assumption",
    "remove_assumption",
    "question",
    "clarification_required",
    "missing_capability",
]


class DraftChangeAgent:
    """Generate a :class:`ChangeProposal` from a user modification request."""

    def generate(
        self,
        draft: ExperimentDraft,
        user_message: str,
        session_id: str | None = None,
    ) -> ChangeProposal:
        """Parse *user_message* and return a structured proposal.

        Raises:
            ValueError: If *draft* is read-only (locked/confirmed). The API
                layer must clone locked drafts before calling this method;
                this check is a defensive safety net.
        """
        if draft.is_read_only():
            raise ValueError(
                "Cannot generate a change proposal on a locked/confirmed "
                "draft. Clone the draft to a new editable version first."
            )

        proposal_id = f"proposal_{uuid.uuid4().hex[:12]}"
        sid = session_id or draft.session_id
        changes: list[DraftChange] = []
        impact_summary: list[str] = []
        invalidates: list[str] = []
        clarifications: list[dict] = []
        missing_caps: list[dict] = []

        msg_lower = user_message.lower()

        # --- Detect parameter changes ---
        changes.extend(self._detect_param_changes(draft, user_message, msg_lower))
        changes.extend(self._detect_design_field_changes(draft, user_message, msg_lower))

        # --- Detect boundary condition changes ---
        bc_changes = self._detect_bc_changes(user_message, msg_lower)
        changes.extend(bc_changes)
        if bc_changes:
            impact_summary.append("边界条件已修改")
            invalidates.append("case_files")

        # --- Detect initial condition changes ---
        ic_changes = self._detect_ic_changes(user_message, msg_lower)
        changes.extend(ic_changes)
        if ic_changes:
            impact_summary.append("初始条件已修改")

        # --- Detect physics model changes ---
        phys_changes = self._detect_physics_changes(user_message, msg_lower)
        changes.extend(phys_changes)
        if phys_changes:
            impact_summary.append("物理模型已修改")
            invalidates.extend(["solver", "numerics"])

        # --- Detect output changes ---
        out_changes = self._detect_output_changes(user_message, msg_lower)
        changes.extend(out_changes)
        if out_changes:
            impact_summary.append("输出变量已修改")
            invalidates.append("measurement_plan")

        # --- Detect mesh changes ---
        if self._has_mesh_keywords(msg_lower):
            changes.append(
                DraftChange(
                    change_type="change_mesh",
                    target_path="mesh",
                    old_value=draft.mesh,
                    new_value=None,
                    reason="用户请求修改网格",
                )
            )
            impact_summary.append("网格策略已修改")
            invalidates.extend(["case_files", "mesh"])

        # --- Detect solver changes ---
        if self._has_solver_keywords(msg_lower):
            solver_name = self._extract_solver_name(user_message)
            changes.append(
                DraftChange(
                    change_type="change_solver",
                    target_path="solver",
                    old_value=draft.solver,
                    new_value={"name": solver_name} if solver_name else None,
                    reason="用户请求更换求解器",
                )
            )
            impact_summary.append("求解器已修改")
            invalidates.extend(["solver", "numerics"])

        # --- Detect questions ---
        if self._is_question(msg_lower):
            changes.append(
                DraftChange(
                    change_type="question",
                    target_path="",
                    old_value=None,
                    new_value=user_message,
                    reason="用户提问",
                )
            )

        # --- If no changes detected, mark as clarification_required ---
        if not changes:
            if self._looks_like_boundary_condition(msg_lower):
                clarifications.append(
                    {
                        "field": "boundary_conditions.boundary",
                        "issue": "缺少需要修改的具体边界",
                        "suggested_question": "哪个边界需要设为自由滑移？",
                    }
                )
            else:
                clarifications.append(
                    {
                        "field": "user_intent",
                        "issue": "无法从用户输入中识别明确的修改意图",
                        "suggested_question": (
                            "请明确您希望修改哪部分：参数、边界条件、"
                            "物理模型、输出变量、网格、还是求解器？"
                        ),
                    }
                )

        return ChangeProposal(
            proposal_id=proposal_id,
            session_id=sid,
            draft_id=draft.draft_id,
            base_draft_version=draft.version,
            summary=self._build_summary(changes, user_message),
            changes=changes,
            impact_summary=impact_summary,
            invalidates=list(set(invalidates)),
            requires_confirmation=True,
            missing_capabilities=missing_caps,
            clarification_required=clarifications,
        )

    # ------------------------------------------------------------------ helpers
    def _detect_param_changes(
        self, draft: ExperimentDraft, message: str, msg_lower: str
    ) -> list[DraftChange]:
        """Detect set_parameter / add_parameter changes."""
        if (
            "展向长度" in message
            or "spanwise" in msg_lower
            or ("lambda2" in msg_lower and ("q" in msg_lower or "q 准则" in msg_lower))
        ):
            return []
        changes: list[DraftChange] = []
        # Pattern: "把 Re 改成 5000" / "Re=5000" / "将直径设为 0.2"
        set_patterns = [
            r"(?:把|将|让)?\s*(\w+)\s*(?:改成|改为|设为|修改为|调整为|换成|更新为)\s*([\d.]+)",
            r"(\w+)\s*[=＝:：]\s*([\d.]+)",
            r"(\w+)\s+(?:设为|改为|改成)\s*([\d.]+)",
        ]
        for pattern in set_patterns:
            for match in re.finditer(pattern, message):
                param_name = match.group(1).strip()
                new_value: Any = match.group(2).strip()
                try:
                    new_value = float(new_value)
                    if new_value == int(new_value):
                        new_value = int(new_value)
                except ValueError:
                    pass

                existing = self._find_param(draft, param_name)
                if existing:
                    changes.append(
                        DraftChange(
                            change_type="set_parameter",
                            target_path=f"control_parameters.{existing.parameter_id}",
                            old_value=existing.value,
                            new_value=new_value,
                            reason=f"用户修改 {param_name}",
                        )
                    )
                else:
                    changes.append(
                        DraftChange(
                            change_type="add_parameter",
                            target_path=f"control_parameters.{param_name}",
                            old_value=None,
                            new_value=new_value,
                            reason=f"用户新增参数 {param_name}",
                        )
                    )
        return changes

    def _detect_design_field_changes(
        self, draft: ExperimentDraft, message: str, msg_lower: str
    ) -> list[DraftChange]:
        """Detect structured design edits that should preserve other fields."""
        changes: list[DraftChange] = []
        span_values = re.findall(r"(\d+(?:\.\d+)?)\s*d", message, re.I)
        if ("展向长度" in message or "spanwise" in msg_lower or "span" in msg_lower) and span_values:
            new_span = f"{span_values[-1]}D"
            domain = {}
            if isinstance(draft.physical_system, dict):
                domain = dict(draft.physical_system.get("computational_domain", {}))
            old_span = domain.get("spanwise_length") or draft.geometry.get("spanwise_length")
            changes.append(
                DraftChange(
                    change_type="change_geometry",
                    target_path="geometry.computational_domain.spanwise_length",
                    old_value=old_span,
                    new_value={"spanwise_length": new_span},
                    reason="用户修改展向长度",
                    confidence=0.95,
                )
            )

        if (
            ("q" in msg_lower or "q 准则" in msg_lower or "q准则" in msg_lower)
            and "lambda2" in msg_lower
        ):
            changes.append(
                DraftChange(
                    change_type="add_output",
                    target_path="requested_outputs.lambda2",
                    old_value="q_criterion",
                    new_value={
                        "metric_id": "lambda2",
                        "display_name": "lambda2 vortex criterion",
                        "replaces": "q_criterion",
                        "category": "scientific",
                    },
                    reason="用户将涡识别准则从 Q criterion 改为 lambda2",
                    confidence=0.95,
                )
            )
        return changes

    def _detect_bc_changes(self, message: str, msg_lower: str) -> list[DraftChange]:
        """Detect boundary condition changes."""
        changes: list[DraftChange] = []
        bc_keywords = [
            "边界", "boundary", "入口", "inlet", "出口", "outlet",
            "壁面", "wall", "上边界", "下边界", "左边界", "右边界",
            "top", "bottom", "left", "right", "自由滑移", "free slip",
            "free_slip",
        ]
        if not any(kw in msg_lower for kw in bc_keywords):
            return changes
        change_keywords = [
            "改成", "改为", "设为", "换成", "修改", "补充", "是",
            "change", "set",
        ]
        if not any(kw in msg_lower for kw in change_keywords):
            return changes

        # Determine which boundary
        boundary = None
        if "入口" in msg_lower or "inlet" in msg_lower:
            boundary = "inlet"
        elif "出口" in msg_lower or "outlet" in msg_lower:
            boundary = "outlet"
        elif "上边界" in msg_lower or "top" in msg_lower:
            boundary = "top"
        elif "下边界" in msg_lower or "bottom" in msg_lower:
            boundary = "bottom"
        elif "左边界" in msg_lower or "left" in msg_lower:
            boundary = "left"
        elif "右边界" in msg_lower or "right" in msg_lower:
            boundary = "right"
        elif "壁面" in msg_lower or "wall" in msg_lower:
            boundary = "wall"

        if boundary:
            bc_type = None
            if (
                "自由滑移" in msg_lower
                or "free slip" in msg_lower
                or "free_slip" in msg_lower
            ):
                bc_type = "free_slip"
            changes.append(
                DraftChange(
                    change_type="change_boundary_condition",
                    target_path=f"boundary_conditions.{boundary}",
                    old_value=None,
                    new_value={"type": bc_type} if bc_type else None,
                    reason=f"用户修改 {boundary} 边界条件",
                    confidence=0.9,
                )
            )
        return changes

    def _looks_like_boundary_condition(self, msg_lower: str) -> bool:
        """Return True when the message mentions a boundary condition."""
        return any(
            keyword in msg_lower
            for keyword in (
                "边界",
                "边界条件",
                "自由滑移",
                "free slip",
                "free_slip",
                "boundary",
            )
        )

    def _detect_ic_changes(self, message: str, msg_lower: str) -> list[DraftChange]:
        """Detect initial condition changes."""
        changes: list[DraftChange] = []
        ic_keywords = ["初始", "initial", "初场", "初始化"]
        if not any(kw in msg_lower for kw in ic_keywords):
            return changes
        change_keywords = ["改成", "改为", "设为", "换成", "修改", "change", "set"]
        if not any(kw in msg_lower for kw in change_keywords):
            return changes
        changes.append(
            DraftChange(
                change_type="change_initial_condition",
                target_path="initial_conditions",
                old_value=None,
                new_value=None,
                reason="用户修改初始条件",
            )
        )
        return changes

    def _detect_physics_changes(self, message: str, msg_lower: str) -> list[DraftChange]:
        """Detect physics model changes."""
        changes: list[DraftChange] = []
        physics_keywords = [
            "物理模型", "physics model", "湍流模型", "turbulence",
            "les", "rans", "des", "大涡", "雷诺平均",
        ]
        if not any(kw in msg_lower for kw in physics_keywords):
            return changes
        change_keywords = ["改成", "改为", "设为", "换成", "用", "use", "switch"]
        if not any(kw in msg_lower for kw in change_keywords):
            return changes

        model = None
        if "les" in msg_lower or "大涡" in msg_lower:
            model = "LES"
        elif "rans" in msg_lower or "雷诺平均" in msg_lower:
            model = "RANS"
        elif "des" in msg_lower:
            model = "DES"

        changes.append(
            DraftChange(
                change_type="change_physics_model",
                target_path="physics_models.turbulence_model",
                old_value=None,
                new_value=model,
                reason=f"用户修改湍流模型为 {model}" if model else "用户修改物理模型",
            )
        )
        return changes

    def _detect_output_changes(self, message: str, msg_lower: str) -> list[DraftChange]:
        """Detect add_output / remove_output changes."""
        changes: list[DraftChange] = []
        add_keywords = ["增加输出", "添加输出", "输出", "增加", "add output", "add"]
        remove_keywords = ["删除输出", "去掉输出", "移除", "remove output", "remove"]
        output_targets = ["阻力", "drag", "升力", "lift", "压力", "pressure", "热", "heat"]

        for target in output_targets:
            if target in msg_lower:
                if any(kw in msg_lower for kw in remove_keywords):
                    changes.append(
                        DraftChange(
                            change_type="remove_output",
                            target_path=f"requested_outputs.{target}",
                            old_value=None,
                            new_value=None,
                            reason=f"用户移除输出 {target}",
                        )
                    )
                elif any(kw in msg_lower for kw in add_keywords):
                    changes.append(
                        DraftChange(
                            change_type="add_output",
                            target_path=f"requested_outputs.{target}",
                            old_value=None,
                            new_value={"name": target},
                            reason=f"用户新增输出 {target}",
                        )
                    )
        return changes

    def _has_mesh_keywords(self, msg_lower: str) -> bool:
        mesh_keywords = ["网格", "mesh", "grid", "加密", "refine", "粗化", "coarsen"]
        change_keywords = ["改成", "改为", "设为", "换成", "修改", "加密", "refine"]
        return any(kw in msg_lower for kw in mesh_keywords) and any(
            kw in msg_lower for kw in change_keywords
        )

    def _has_solver_keywords(self, msg_lower: str) -> bool:
        solver_keywords = [
            "求解器", "solver", "pimplefoam", "simplefoam", "pisofoam",
            "icofoam", "rhofoam", "buoyant",
        ]
        change_keywords = ["改成", "改为", "换成", "用", "use", "switch"]
        return any(kw in msg_lower for kw in solver_keywords) and any(
            kw in msg_lower for kw in change_keywords
        )

    def _extract_solver_name(self, message: str) -> str | None:
        msg_lower = message.lower()
        solvers = [
            "pimplefoam", "simplefoam", "pisofoam", "icofoam",
            "rhofoam", "buoyantpimplefoam", "buoyantsimplefoam",
        ]
        for s in solvers:
            if s in msg_lower:
                return s
        return None

    def _is_question(self, msg_lower: str) -> bool:
        question_keywords = [
            "为什么", "是什么", "什么意思", "有什么影响",
            "why", "what", "how come", "meaning",
        ]
        return any(kw in msg_lower for kw in question_keywords)

    def _find_param(
        self, draft: ExperimentDraft, name: str
    ) -> DraftParameter | None:
        name_lower = name.lower()
        for p in draft.control_parameters:
            if (
                p.parameter_id.lower() == name_lower
                or p.display_name.lower() == name_lower
                or name_lower in p.display_name.lower()
                or name_lower in p.parameter_id.lower()
            ):
                return p
        return None

    def _build_summary(self, changes: list[DraftChange], user_message: str) -> str:
        if not changes:
            return f"需要澄清用户意图: {user_message[:50]}"
        type_counts: dict[str, int] = {}
        for c in changes:
            ct = c.change_type
            type_counts[ct] = type_counts.get(ct, 0) + 1
        parts = [f"{count}个{ct}" for ct, count in type_counts.items()]
        return f"修改提案包含: {', '.join(parts)}"


__all__ = ["DraftChangeAgent", "CHANGE_TYPES"]
