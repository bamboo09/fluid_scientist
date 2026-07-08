"""RequirementGraph — generates parameter slots from physics spec.

Given a PhysicsSpecResult and metric plan, determines which parameter
slots the experiment needs. This is NOT template concatenation —
slots are derived from physical closure requirements.
"""
from __future__ import annotations

from typing import Any

from fluid_scientist.workbench.physics_spec_builder import PhysicsSpecResult


class RequiredSlot:
    """A parameter slot required by the experiment."""

    def __init__(
        self,
        slot_id: str,
        display_name: str,
        category: str,
        reason: str,
        required_by: list[str] | None = None,
        criticality: str = "medium",
        acceptable_sources: list[str] | None = None,
        dependencies: list[str] | None = None,
        affects: list[str] | None = None,
    ):
        self.slot_id = slot_id
        self.display_name = display_name
        self.category = category
        self.reason = reason
        self.required_by = required_by or []
        self.criticality = criticality
        self.acceptable_sources = acceptable_sources or [
            "user", "derived", "model_recommended",
        ]
        self.dependencies = dependencies or []
        self.affects = affects or []

    def to_dict(self) -> dict:
        return {
            "slot_id": self.slot_id,
            "display_name": self.display_name,
            "category": self.category,
            "reason": self.reason,
            "required_by": self.required_by,
            "criticality": self.criticality,
            "acceptable_sources": self.acceptable_sources,
            "dependencies": self.dependencies,
            "affects": self.affects,
        }


class RequirementGraph:
    """Generates parameter slots from physics spec and metric plan."""

    def plan(
        self,
        physics_spec: PhysicsSpecResult,
        metric_plan: list[dict] | None = None,
        user_values: dict[str, Any] | None = None,
    ) -> list[RequiredSlot]:
        """Generate required parameter slots.

        Args:
            physics_spec: Physics specification from PhysicsSpecBuilder
            metric_plan: Metric plan from MetricPlanner
            user_values: User-provided values

        Returns:
            List of RequiredSlot objects
        """
        if user_values is None:
            user_values = {}
        if metric_plan is None:
            metric_plan = []

        slots: list[RequiredSlot] = []
        system = physics_spec.physical_system
        geometry = physics_spec.geometry_type

        # 1. Geometry slots (always needed)
        slots.extend(self._geometry_slots(system, geometry))

        # 2. Material property slots
        slots.extend(self._material_slots(physics_spec))

        # 3. Boundary condition slots
        slots.extend(self._boundary_slots(system, physics_spec))

        # 4. Initial condition slots
        slots.extend(self._initial_condition_slots(physics_spec))

        # 5. Numerics slots
        slots.extend(self._numerics_slots(physics_spec))

        # 6. Mesh slots
        slots.extend(self._mesh_slots(system))

        # 7. Turbulence model slots
        slots.extend(self._turbulence_slots(physics_spec))

        # 7.5. Flow condition slots (physical quantities)
        slots.extend(self._flow_condition_slots(system, physics_spec))

        # 7.6. Derived parameter slots
        slots.extend(self._derived_slots(physics_spec))

        # 8. Metric-driven measurement slots
        slots.extend(self._measurement_slots(metric_plan))

        # 9. Compute resource slots
        slots.extend(self._compute_slots())

        return slots

    def _geometry_slots(
        self, system: str, geometry: str,
    ) -> list[RequiredSlot]:
        """Generate geometry parameter slots based on physical system."""
        slots = []

        if system == "pipe_flow" or geometry == "pipe":
            slots.append(RequiredSlot(
                slot_id="geometry.diameter",
                display_name="管径",
                category="geometry",
                reason="特征长度，用于雷诺数计算和网格生成",
                required_by=["solver", "reynolds_number", "mesh"],
                criticality="critical",
                affects=["reynolds_number", "mesh", "force_coefficients"],
            ))
            slots.append(RequiredSlot(
                slot_id="geometry.length",
                display_name="管长",
                category="geometry",
                reason="流动域长度",
                required_by=["solver", "mesh"],
                criticality="high",
                affects=["mesh"],
            ))
        elif system == "external_flow" or geometry == "cylinder":
            slots.append(RequiredSlot(
                slot_id="geometry.diameter",
                display_name="圆柱直径",
                category="geometry",
                reason="特征长度，用于雷诺数和力系数计算",
                required_by=["solver", "reynolds_number", "force_coefficients"],
                criticality="critical",
                affects=["reynolds_number", "mesh", "force_coefficients"],
            ))
            slots.append(RequiredSlot(
                slot_id="geometry.domain_width",
                display_name="计算域宽度",
                category="geometry",
                reason="外流场宽度，影响边界效应",
                required_by=["mesh"],
                criticality="medium",
                affects=["mesh"],
            ))
            slots.append(RequiredSlot(
                slot_id="geometry.domain_height",
                display_name="计算域高度",
                category="geometry",
                reason="外流场高度，影响尾迹发展",
                required_by=["mesh"],
                criticality="medium",
                affects=["mesh"],
            ))
        elif system == "cavity_flow" or geometry == "cavity":
            slots.append(RequiredSlot(
                slot_id="geometry.side_length",
                display_name="方腔边长",
                category="geometry",
                reason="特征长度",
                required_by=["solver", "reynolds_number"],
                criticality="critical",
                affects=["reynolds_number", "mesh"],
            ))
        else:
            # Generic geometry
            slots.append(RequiredSlot(
                slot_id="geometry.characteristic_length",
                display_name="特征长度",
                category="geometry",
                reason="特征长度用于雷诺数和无量纲化",
                required_by=["solver", "reynolds_number"],
                criticality="critical",
                affects=["reynolds_number", "mesh"],
            ))

        return slots

    def _material_slots(
        self, spec: PhysicsSpecResult,
    ) -> list[RequiredSlot]:
        """Generate material property slots."""
        slots = []

        slots.append(RequiredSlot(
            slot_id="material.density",
            display_name="密度",
            category="material_property",
            reason="流体密度，用于雷诺数和质量流量计算",
            required_by=["solver", "reynolds_number", "mass_flow_rate"],
            criticality="critical",
            dependencies=["material_or_fluid_name"],
            affects=["reynolds_number", "force_coefficients"],
        ))
        slots.append(RequiredSlot(
            slot_id="material.kinematic_viscosity",
            display_name="运动粘度",
            category="material_property",
            reason="流体运动粘度，用于雷诺数计算",
            required_by=["solver", "reynolds_number"],
            criticality="critical",
            dependencies=["material_or_fluid_name"],
            affects=["reynolds_number"],
        ))

        if spec.thermal_model == "heat_transfer":
            slots.append(RequiredSlot(
                slot_id="material.thermal_conductivity",
                display_name="热导率",
                category="material_property",
                reason="热导率用于能量方程",
                required_by=["solver"],
                criticality="high",
            ))
            slots.append(RequiredSlot(
                slot_id="material.specific_heat",
                display_name="比热容",
                category="material_property",
                reason="比热容用于能量方程",
                required_by=["solver"],
                criticality="high",
            ))

        return slots

    def _boundary_slots(
        self, system: str, spec: PhysicsSpecResult,
    ) -> list[RequiredSlot]:
        """Generate boundary condition slots."""
        slots = []

        # Inlet boundary
        slots.append(RequiredSlot(
            slot_id="boundary.inlet_condition",
            display_name="入口边界条件",
            category="boundary_condition",
            reason="入口边界用于闭合流动方程",
            required_by=["solver"],
            criticality="critical",
            affects=["reynolds_number", "mesh", "force_coefficients"],
        ))

        # Outlet boundary
        slots.append(RequiredSlot(
            slot_id="boundary.outlet_condition",
            display_name="出口边界条件",
            category="boundary_condition",
            reason="出口边界用于闭合压力场",
            required_by=["solver"],
            criticality="critical",
        ))

        # Wall boundary
        slots.append(RequiredSlot(
            slot_id="boundary.wall_condition",
            display_name="壁面边界条件",
            category="boundary_condition",
            reason="壁面条件（无滑移/滑移/粗糙度）",
            required_by=["solver"],
            criticality="high",
        ))

        # External flow specific
        if system == "external_flow":
            slots.append(RequiredSlot(
                slot_id="boundary.farfield",
                display_name="远场边界",
                category="boundary_condition",
                reason="外流问题需要远场边界条件",
                required_by=["solver"],
                criticality="high",
            ))

        # Cavity specific
        if system == "cavity_flow":
            slots.append(RequiredSlot(
                slot_id="boundary.lid_velocity",
                display_name="盖板速度",
                category="boundary_condition",
                reason="方腔流驱动条件",
                required_by=["solver", "reynolds_number"],
                criticality="critical",
                affects=["reynolds_number"],
            ))

        return slots

    def _initial_condition_slots(
        self, spec: PhysicsSpecResult,
    ) -> list[RequiredSlot]:
        """Generate initial condition slots."""
        slots = []

        slots.append(RequiredSlot(
            slot_id="initial.velocity_field",
            display_name="初始速度场",
            category="initial_condition",
            reason="求解器需要初始速度场",
            required_by=["solver"],
            criticality="medium",
            acceptable_sources=["advanced_default", "model_recommended"],
        ))
        slots.append(RequiredSlot(
            slot_id="initial.pressure_field",
            display_name="初始压力场",
            category="initial_condition",
            reason="求解器需要初始压力场",
            required_by=["solver"],
            criticality="medium",
            acceptable_sources=["advanced_default", "model_recommended"],
        ))

        if spec.temporal_type == "transient":
            slots.append(RequiredSlot(
                slot_id="initial.turbulence_fields",
                display_name="初始湍流场",
                category="initial_condition",
                reason="瞬态湍流计算需要初始湍动能和耗散率",
                required_by=["solver"],
                criticality="medium",
                acceptable_sources=["advanced_default"],
            ))

        return slots

    def _numerics_slots(
        self, spec: PhysicsSpecResult,
    ) -> list[RequiredSlot]:
        """Generate numerics parameter slots."""
        slots = []

        slots.append(RequiredSlot(
            slot_id="numerics.solver",
            display_name="求解器",
            category="numerics",
            reason="OpenFOAM 求解器选择",
            required_by=["compilation"],
            criticality="critical",
            acceptable_sources=["model_recommended", "user"],
        ))
        slots.append(RequiredSlot(
            slot_id="numerics.time_step",
            display_name="时间步长",
            category="numerics",
            reason="时间推进步长，影响 Courant 数",
            required_by=["solver"],
            criticality="high",
            dependencies=["max_courant", "inlet_velocity", "mesh"],
            affects=["courant_number"],
        ))
        slots.append(RequiredSlot(
            slot_id="numerics.end_time",
            display_name="结束时间",
            category="numerics",
            reason="仿真总时间",
            required_by=["solver"],
            criticality="high",
        ))
        slots.append(RequiredSlot(
            slot_id="numerics.max_courant",
            display_name="最大 Courant 数",
            category="numerics",
            reason="Courant 数上限，控制时间步",
            required_by=["solver"],
            criticality="medium",
            acceptable_sources=["model_recommended", "advanced_default"],
        ))

        return slots

    def _mesh_slots(self, system: str) -> list[RequiredSlot]:
        """Generate mesh parameter slots."""
        slots = []

        slots.append(RequiredSlot(
            slot_id="mesh.cells",
            display_name="网格数量",
            category="mesh",
            reason="网格分辨率",
            required_by=["solver"],
            criticality="high",
            acceptable_sources=["model_recommended", "user"],
        ))

        if system == "pipe_flow":
            slots.append(RequiredSlot(
                slot_id="mesh.axial_cells",
                display_name="轴向网格数",
                category="mesh",
                reason="轴向网格分辨率",
                criticality="medium",
            ))
            slots.append(RequiredSlot(
                slot_id="mesh.radial_cells",
                display_name="径向网格数",
                category="mesh",
                reason="径向网格分辨率",
                criticality="medium",
            ))
        elif system == "external_flow":
            slots.append(RequiredSlot(
                slot_id="mesh.cells_radial",
                display_name="径向网格数",
                category="mesh",
                reason="圆柱周围径向网格",
                criticality="medium",
            ))
            slots.append(RequiredSlot(
                slot_id="mesh.cells_wake",
                display_name="尾迹网格数",
                category="mesh",
                reason="尾迹区域网格加密",
                criticality="medium",
            ))
        elif system == "cavity_flow":
            slots.append(RequiredSlot(
                slot_id="mesh.cells_per_side",
                display_name="每边网格数",
                category="mesh",
                reason="方腔网格分辨率",
                criticality="medium",
            ))

        return slots

    def _turbulence_slots(
        self, spec: PhysicsSpecResult,
    ) -> list[RequiredSlot]:
        """Generate turbulence model slots."""
        slots = []

        if spec.flow_regime in ("turbulent", "transitional", "unknown"):
            slots.append(RequiredSlot(
                slot_id="turbulence.model",
                display_name="湍流模型",
                category="turbulence_model",
                reason="湍流模型选择（kOmegaSST, kEpsilon, etc.）",
                required_by=["solver"],
                criticality="high",
                acceptable_sources=["model_recommended", "user"],
            ))

        return slots

    def _flow_condition_slots(
        self, system: str, spec: PhysicsSpecResult,
    ) -> list[RequiredSlot]:
        """Generate flow condition slots (physical quantities)."""
        slots = []

        if system in ("pipe_flow", "external_flow", "channel_flow"):
            slots.append(RequiredSlot(
                slot_id="flow.inlet_velocity",
                display_name="入口速度",
                category="flow_condition",
                reason="入口速度大小，用于雷诺数计算",
                required_by=["solver", "reynolds_number"],
                criticality="critical",
                affects=["reynolds_number"],
            ))
            slots.append(RequiredSlot(
                slot_id="flow.mass_flow_rate",
                display_name="质量流量",
                category="flow_condition",
                reason="质量流量，可推导平均速度",
                required_by=["mean_velocity"],
                criticality="high",
                affects=["mean_velocity"],
            ))

        return slots

    def _derived_slots(
        self, spec: PhysicsSpecResult,
    ) -> list[RequiredSlot]:
        """Generate derived parameter slots."""
        slots = []

        # Reynolds number (always needed for flow problems)
        slots.append(RequiredSlot(
            slot_id="derived.reynolds_number",
            display_name="雷诺数",
            category="derived",
            reason="雷诺数判断流动状态",
            required_by=["solver", "turbulence_model"],
            criticality="high",
            acceptable_sources=["derived", "user", "model_recommended"],
            dependencies=["inlet_velocity", "diameter", "kinematic_viscosity"],
        ))

        # Mean velocity (for pipe flow with mass flow rate)
        if spec.physical_system == "pipe_flow":
            slots.append(RequiredSlot(
                slot_id="derived.mean_velocity",
                display_name="平均速度",
                category="derived",
                reason="管道截面平均速度",
                required_by=["reynolds_number"],
                criticality="medium",
                acceptable_sources=["derived", "user", "model_recommended"],
                dependencies=["mass_flow_rate", "density", "diameter"],
            ))

        return slots

    def _measurement_slots(
        self, metric_plan: list[dict],
    ) -> list[RequiredSlot]:
        """Generate measurement slots from metric plan."""
        slots = []

        for metric in metric_plan:
            metric_id = metric.get("metric_id", "")
            required_data = metric.get("required_data", [])

            for data_req in required_data:
                slot_id = f"measurement.{metric_id}.{data_req}"
                slots.append(RequiredSlot(
                    slot_id=slot_id,
                    display_name=f"{metric_id} 采样: {data_req}",
                    category="measurement",
                    reason=f"指标 {metric_id} 需要采样 {data_req}",
                    required_by=[metric_id],
                    criticality=(
                        "high" if metric.get("metric_type") == "core"
                        else "medium"
                    ),
                ))

        return slots

    def _compute_slots(self) -> list[RequiredSlot]:
        """Generate compute resource slots."""
        return [
            RequiredSlot(
                slot_id="compute.cores",
                display_name="计算核心数",
                category="compute",
                reason="并行计算核心数",
                criticality="low",
                acceptable_sources=["model_recommended", "advanced_default"],
            ),
            RequiredSlot(
                slot_id="compute.estimated_time",
                display_name="预估计算时间",
                category="compute",
                reason="预估仿真运行时间",
                criticality="low",
                acceptable_sources=["model_recommended"],
            ),
        ]
