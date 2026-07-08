"""参数语义冲突检测器。

检测用户修改参数后产生的语义冲突，例如：
- 用户先说"保持 Re=100"，之后把速度改成 2 m/s
- 系统必须检测速度和 Reynolds 数冲突
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ParameterConflict:
    """检测到的参数冲突。"""
    conflict_id: str
    parameter_a: str
    parameter_b: str
    conflict_type: str  # dependency_violation, unit_inconsistency, physics_violation
    description: str
    current_values: dict[str, Any] = field(default_factory=dict)
    resolution_suggestion: str | None = None


# 已知参数依赖关系
# key: 参数ID, value: 该参数依赖的其他参数及其关系
_DEPENDENCY_RULES: dict[str, dict[str, str]] = {
    "reynolds_number": {
        "depends_on": "inlet_velocity",
        "formula": "Re = U * D / nu",
        "description": "雷诺数由速度、直径和运动粘度共同决定",
    },
    "inlet_velocity": {
        "depends_on": "reynolds_number",
        "formula": "U = Re * nu / D",
        "description": "入口速度可由雷诺数、运动粘度和直径推导",
    },
    "mass_flow_rate": {
        "depends_on": "inlet_velocity",
        "formula": "m_dot = rho * U * A",
        "description": "质量流量由密度、速度和截面积决定",
    },
}


class ConflictDetector:
    """检测参数间的语义冲突。"""

    def detect_conflicts(
        self,
        parameters: dict[str, Any],
        changed_parameter_id: str | None = None,
    ) -> list[ParameterConflict]:
        """检测参数字典中的语义冲突。

        Args:
            parameters: 参数ID到值的映射。
            changed_parameter_id: 刚被修改的参数ID，用于定向检测。

        Returns:
            检测到的冲突列表。
        """
        conflicts: list[ParameterConflict] = []

        for param_id, rule in _DEPENDENCY_RULES.items():
            if param_id not in parameters:
                continue
            depends_on = rule["depends_on"]
            if depends_on not in parameters:
                continue

            # 检查 Reynolds 数与速度的一致性
            if param_id == "reynolds_number" and depends_on == "inlet_velocity":
                conflict = self._check_reynolds_velocity_consistency(
                    parameters, changed_parameter_id
                )
                if conflict:
                    conflicts.append(conflict)

            # 检查质量流量与速度的一致性
            if param_id == "mass_flow_rate" and depends_on == "inlet_velocity":
                conflict = self._check_massflow_velocity_consistency(
                    parameters, changed_parameter_id
                )
                if conflict:
                    conflicts.append(conflict)

        return conflicts

    @staticmethod
    def _check_reynolds_velocity_consistency(
        parameters: dict[str, Any],
        changed_parameter_id: str | None,
    ) -> ParameterConflict | None:
        """检查 Reynolds 数与入口速度的一致性。"""
        re = parameters.get("reynolds_number")
        velocity = parameters.get("inlet_velocity")
        diameter = parameters.get("diameter")
        viscosity = parameters.get("kinematic_viscosity")

        if re is None or velocity is None:
            return None

        # 如果有直径和粘度，可以验证
        if diameter is not None and viscosity is not None and viscosity != 0:
            calculated_re = velocity * diameter / viscosity
            relative_error = abs(calculated_re - re) / max(abs(re), 1.0)

            if relative_error > 0.05:  # 5% 容差
                changed = changed_parameter_id or "unknown"
                return ParameterConflict(
                    conflict_id=f"conflict_re_vel_{changed}",
                    parameter_a="reynolds_number",
                    parameter_b="inlet_velocity",
                    conflict_type="dependency_violation",
                    description=(
                        f"雷诺数 Re={re} 与速度 U={velocity} m/s 不一致。"
                        f"根据 U={velocity}, D={diameter}, nu={viscosity}，"
                        f"计算得 Re={calculated_re:.1f}，偏差 {relative_error*100:.1f}%"
                    ),
                    current_values={
                        "reynolds_number": re,
                        "inlet_velocity": velocity,
                        "diameter": diameter,
                        "kinematic_viscosity": viscosity,
                    },
                    resolution_suggestion=(
                        f"请确认：是保持 Re={re}（则速度应调整为 "
                        f"{re * viscosity / diameter:.4f} m/s），"
                        f"还是保持速度 U={velocity} m/s（则 Re={calculated_re:.1f}）？"
                    ),
                )
        return None

    @staticmethod
    def _check_massflow_velocity_consistency(
        parameters: dict[str, Any],
        changed_parameter_id: str | None,
    ) -> ParameterConflict | None:
        """检查质量流量与入口速度的一致性。"""
        mass_flow = parameters.get("mass_flow_rate")
        velocity = parameters.get("inlet_velocity")
        density = parameters.get("density")
        diameter = parameters.get("diameter")

        if mass_flow is None or velocity is None:
            return None

        if density is not None and diameter is not None:
            import math
            area = math.pi * (diameter / 2) ** 2
            calculated_mdot = density * velocity * area
            relative_error = abs(calculated_mdot - mass_flow) / max(abs(mass_flow), 1e-12)

            if relative_error > 0.05:
                changed = changed_parameter_id or "unknown"
                return ParameterConflict(
                    conflict_id=f"conflict_mdot_vel_{changed}",
                    parameter_a="mass_flow_rate",
                    parameter_b="inlet_velocity",
                    conflict_type="dependency_violation",
                    description=(
                        f"质量流量 m_dot={mass_flow} 与速度 U={velocity} m/s 不一致。"
                        f"根据 rho={density}, U={velocity}, D={diameter}，"
                        f"计算得 m_dot={calculated_mdot:.6f}，偏差 {relative_error*100:.1f}%"
                    ),
                    current_values={
                        "mass_flow_rate": mass_flow,
                        "inlet_velocity": velocity,
                        "density": density,
                        "diameter": diameter,
                    },
                    resolution_suggestion=(
                        f"请确认：是保持质量流量（则速度应调整为 "
                        f"{mass_flow / (density * area):.4f} m/s），"
                        f"还是保持速度（则质量流量为 {calculated_mdot:.6f}）？"
                    ),
                )
        return None


__all__ = ["ConflictDetector", "ParameterConflict"]
