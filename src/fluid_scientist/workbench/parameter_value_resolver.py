"""ParameterValueResolver — fills values for parameter slots.

Given RequiredSlots from RequirementGraph, fills each slot with
user values, derived values, model recommendations, or unknown status.
The goal is to produce a draft parameter table that is NOT empty.
"""
from __future__ import annotations

import math
from typing import Any

from fluid_scientist.workbench.physics_spec_builder import PhysicsSpecResult
from fluid_scientist.workbench.requirement_graph import RequiredSlot

# Material database
MATERIAL_DB = {
    "water": {
        "density": {
            "value": 998.2, "unit": "kg/m3",
            "reason": "室温水典型密度", "confidence": "high",
        },
        "kinematic_viscosity": {
            "value": 1e-6, "unit": "m2/s",
            "reason": "室温水典型运动黏度", "confidence": "high",
        },
        "thermal_conductivity": {
            "value": 0.6, "unit": "W/(m·K)",
            "reason": "水热导率", "confidence": "high",
        },
        "specific_heat": {
            "value": 4182, "unit": "J/(kg·K)",
            "reason": "水比热容", "confidence": "high",
        },
    },
    "air": {
        "density": {
            "value": 1.225, "unit": "kg/m3",
            "reason": "标准大气压空气密度", "confidence": "high",
        },
        "kinematic_viscosity": {
            "value": 1.5e-5, "unit": "m2/s",
            "reason": "空气运动黏度", "confidence": "high",
        },
        "thermal_conductivity": {
            "value": 0.0257, "unit": "W/(m·K)",
            "reason": "空气热导率", "confidence": "high",
        },
        "specific_heat": {
            "value": 1005, "unit": "J/(kg·K)",
            "reason": "空气比热容", "confidence": "high",
        },
    },
    "oil": {
        "density": {
            "value": 870, "unit": "kg/m3",
            "reason": "典型油密度", "confidence": "medium",
        },
        "kinematic_viscosity": {
            "value": 1e-4, "unit": "m2/s",
            "reason": "典型油运动黏度", "confidence": "medium",
        },
    },
}

# Solver recommendations
SOLVER_DB = {
    ("incompressible", "steady", "single_phase"): (
        "simpleFoam", "不可压稳态单相",
    ),
    ("incompressible", "transient", "single_phase"): (
        "pimpleFoam", "不可压瞬态单相",
    ),
    ("compressible", "steady", "single_phase"): (
        "rhoSimpleFoam", "可压稳态单相",
    ),
    ("compressible", "transient", "single_phase"): (
        "rhoPimpleFoam", "可压瞬态单相",
    ),
}

# Turbulence model recommendations
TURBULENCE_DB = {
    "turbulent": (
        "kOmegaSST", "kOmegaSST 适用于壁面流动和逆压梯度",
    ),
    "transitional": ("kOmegaSST", "过渡区推荐 kOmegaSST"),
}


class ResolvedParameter:
    """A parameter with resolved value and metadata."""

    def __init__(
        self,
        slot_id: str,
        display_name: str,
        category: str,
        value: Any | None = None,
        unit: str | None = None,
        status: str = "unknown_required",
        source: str = "unknown",
        reason: str = "",
        confidence: str = "low",
        risk: str = "low",
        confirmation_policy: str = "none",
        criticality: str = "medium",
        editable: bool = True,
        dependencies: list[str] | None = None,
        affects: list[str] | None = None,
    ):
        self.slot_id = slot_id
        self.display_name = display_name
        self.category = category
        self.value = value
        self.unit = unit
        self.status = status
        self.source = source
        self.reason = reason
        self.confidence = confidence
        self.risk = risk
        self.confirmation_policy = confirmation_policy
        self.criticality = criticality
        self.editable = editable
        self.dependencies = dependencies or []
        self.affects = affects or []

    def to_dict(self) -> dict:
        return {
            "parameter_id": self.slot_id,
            "display_name": self.display_name,
            "category": self.category,
            "value": self.value,
            "unit": self.unit,
            "status": self.status,
            "source": self.source,
            "reason": self.reason,
            "confidence": self.confidence,
            "risk": self.risk,
            "confirmation_policy": self.confirmation_policy,
            "criticality": self.criticality,
            "editable": self.editable,
            "dependencies": self.dependencies,
            "affects": self.affects,
        }


class ParameterValueResolver:
    """Resolves values for parameter slots."""

    def resolve(
        self,
        slots: list[RequiredSlot],
        physics_spec: PhysicsSpecResult,
        user_values: dict[str, Any] | None = None,
    ) -> list[ResolvedParameter]:
        """Resolve values for all slots.

        Filling priority:
        1. User explicitly provided
        2. Unit normalization
        3. Deterministic formula derivation
        4. Physical model recommendation
        5. Experience range recommendation
        6. Numerical strategy recommendation
        7. advanced_default
        8. unknown_required
        """
        if user_values is None:
            user_values = {}

        # Also check physics_spec.known_conditions
        known = physics_spec.known_conditions or {}
        for key, val in known.items():
            if key not in user_values:
                if isinstance(val, dict):
                    user_values[key] = val.get("value")
                else:
                    user_values[key] = val

        resolved: list[ResolvedParameter] = []

        for slot in slots:
            param = self._resolve_slot(
                slot, physics_spec, user_values, resolved,
            )
            resolved.append(param)

        # Second pass: compute derived values
        resolved = self._compute_derived(resolved)

        return resolved

    def _resolve_slot(
        self,
        slot: RequiredSlot,
        spec: PhysicsSpecResult,
        user_values: dict[str, Any],
        already_resolved: list[ResolvedParameter],
    ) -> ResolvedParameter:
        """Resolve a single slot."""
        param = ResolvedParameter(
            slot_id=slot.slot_id,
            display_name=slot.display_name,
            category=slot.category,
            criticality=slot.criticality,
            dependencies=slot.dependencies,
            affects=slot.affects,
        )

        # Extract short key from slot_id (e.g. "diameter" from
        # "geometry.diameter")
        short_key = slot.slot_id.split(".")[-1]

        # 1. Check user values
        if short_key in user_values and user_values[short_key] is not None:
            param.value = user_values[short_key]
            param.status = "user_confirmed"
            param.source = "user"
            param.reason = "用户明确提供"
            param.confidence = "high"
            param.confirmation_policy = "none"
            return param

        # Also check full slot_id
        if (
            slot.slot_id in user_values
            and user_values[slot.slot_id] is not None
        ):
            param.value = user_values[slot.slot_id]
            param.status = "user_confirmed"
            param.source = "user"
            param.reason = "用户明确提供"
            param.confidence = "high"
            param.confirmation_policy = "none"
            return param

        # 2. Material database recommendations
        material = spec.material_or_fluid_name
        if material in MATERIAL_DB and short_key in MATERIAL_DB[material]:
            db_entry = MATERIAL_DB[material][short_key]
            param.value = db_entry["value"]
            param.unit = db_entry["unit"]
            param.status = "model_recommended"
            param.source = "material_database"
            param.reason = db_entry["reason"]
            param.confidence = db_entry.get("confidence", "medium")
            param.confirmation_policy = "recommend_and_notify"
            return param

        # 3. Solver recommendations
        if short_key == "solver":
            solver, reason = self._recommend_solver(spec)
            if solver:
                param.value = solver
                param.unit = None
                param.status = "model_recommended"
                param.source = "model_recommended"
                param.reason = reason
                param.confidence = "high"
                param.confirmation_policy = "recommend_and_notify"
                return param

        # 4. Turbulence model recommendations
        if short_key == "model" and slot.category == "turbulence_model":
            model, reason = self._recommend_turbulence(spec)
            if model:
                param.value = model
                param.status = "model_recommended"
                param.source = "model_recommended"
                param.reason = reason
                param.confidence = "medium"
                param.confirmation_policy = "recommend_and_notify"
                return param

        # 5. Boundary condition recommendations
        if slot.category == "boundary_condition":
            bc_rec = self._recommend_boundary(slot, spec)
            if bc_rec:
                param.value = bc_rec["value"]
                param.unit = bc_rec.get("unit")
                param.status = bc_rec.get(
                    "status", "model_recommended",
                )
                param.source = "model_recommended"
                param.reason = bc_rec.get("reason", "")
                param.confidence = bc_rec.get("confidence", "medium")
                param.confirmation_policy = bc_rec.get(
                    "confirmation_policy", "recommend_and_notify",
                )
                return param

        # 6. Initial condition recommendations
        if slot.category == "initial_condition":
            ic_rec = self._recommend_initial_condition(slot, spec)
            if ic_rec:
                param.value = ic_rec["value"]
                param.unit = ic_rec.get("unit")
                param.status = ic_rec.get("status", "advanced_default")
                param.source = "advanced_default"
                param.reason = ic_rec.get("reason", "")
                param.confidence = "low"
                param.confirmation_policy = "none"
                return param

        # 7. Numerics recommendations
        if slot.category == "numerics":
            num_rec = self._recommend_numerics(slot, spec)
            if num_rec:
                param.value = num_rec["value"]
                param.unit = num_rec.get("unit")
                param.status = num_rec.get(
                    "status", "model_recommended",
                )
                param.source = num_rec.get("source", "model_recommended")
                param.reason = num_rec.get("reason", "")
                param.confidence = num_rec.get("confidence", "medium")
                param.confirmation_policy = num_rec.get(
                    "confirmation_policy", "recommend_and_notify",
                )
                return param

        # 8. Mesh recommendations
        if slot.category == "mesh":
            mesh_rec = self._recommend_mesh(slot, spec)
            if mesh_rec:
                param.value = mesh_rec["value"]
                param.status = mesh_rec.get("status", "model_recommended")
                param.source = "model_recommended"
                param.reason = mesh_rec.get("reason", "")
                param.confidence = "medium"
                param.confirmation_policy = "recommend_and_notify"
                return param

        # 9. Compute recommendations
        if slot.category == "compute":
            param.value = 4 if short_key == "cores" else None
            param.status = "advanced_default"
            param.source = "advanced_default"
            param.reason = "默认值"
            param.confirmation_policy = "none"
            return param

        # 10. Default: unknown_required
        param.status = "unknown_required"
        param.source = "unknown"
        param.reason = "当前无法推荐，需要用户补充"
        param.confirmation_policy = "blocking"

        return param

    def _compute_derived(
        self, params: list[ResolvedParameter],
    ) -> list[ResolvedParameter]:
        """Compute derived parameter values."""
        param_map = {p.slot_id.split(".")[-1]: p for p in params}

        # Compute mean_velocity from mass_flow_rate, density, diameter
        if (
            "mean_velocity" in param_map
            and param_map["mean_velocity"].value is None
        ):
            m_dot = param_map.get("mass_flow_rate")
            rho = param_map.get("density")
            d = param_map.get("diameter")
            if (
                m_dot and m_dot.value is not None
                and rho and rho.value is not None
                and d and d.value is not None and d.value > 0
            ):
                area = math.pi * d.value ** 2 / 4
                param_map["mean_velocity"].value = (
                    m_dot.value / (rho.value * area)
                )
                param_map["mean_velocity"].status = "derived"
                param_map["mean_velocity"].source = "derived"
                param_map["mean_velocity"].reason = (
                    "由质量流量、密度和管径推导"
                )
                param_map["mean_velocity"].confidence = "high"

        # Compute reynolds_number
        if (
            "reynolds_number" in param_map
            and param_map["reynolds_number"].value is None
        ):
            u = (
                param_map.get("inlet_velocity")
                or param_map.get("mean_velocity")
                or param_map.get("lid_velocity")
            )
            d = (
                param_map.get("diameter")
                or param_map.get("side_length")
                or param_map.get("characteristic_length")
            )
            nu = param_map.get("kinematic_viscosity")
            if (
                u and u.value is not None
                and d and d.value is not None
                and nu and nu.value is not None and nu.value > 0
            ):
                param_map["reynolds_number"].value = (
                    u.value * d.value / nu.value
                )
                param_map["reynolds_number"].status = "derived"
                param_map["reynolds_number"].source = "derived"
                param_map["reynolds_number"].reason = (
                    "由特征速度、特征长度和运动粘度推导"
                )
                param_map["reynolds_number"].confidence = "high"

        return params

    def _recommend_solver(
        self, spec: PhysicsSpecResult,
    ) -> tuple[str | None, str]:
        """Recommend OpenFOAM solver."""
        comp = (
            spec.compressibility
            if spec.compressibility != "unknown"
            else "incompressible"
        )
        temporal = (
            spec.temporal_type
            if spec.temporal_type != "unknown"
            else "steady"
        )
        phase = (
            spec.phase_model
            if spec.phase_model != "unknown"
            else "single_phase"
        )

        key = (comp, temporal, phase)
        if key in SOLVER_DB:
            solver, reason = SOLVER_DB[key]
            return solver, reason

        return "simpleFoam", "默认不可压稳态求解器"

    def _recommend_turbulence(
        self, spec: PhysicsSpecResult,
    ) -> tuple[str | None, str]:
        if spec.flow_regime in TURBULENCE_DB:
            return TURBULENCE_DB[spec.flow_regime]
        return None, ""

    def _recommend_boundary(
        self, slot: RequiredSlot, spec: PhysicsSpecResult,
    ) -> dict | None:
        """Recommend boundary condition value."""
        if "inlet" in slot.slot_id:
            if spec.physical_system == "pipe_flow":
                return {
                    "value": "velocity_inlet",
                    "reason": "管流通常用速度入口",
                    "confidence": "medium",
                    "confirmation_policy": "recommend_and_notify",
                }
            return {
                "value": "velocity_inlet",
                "reason": "默认速度入口",
                "confidence": "low",
                "confirmation_policy": "require_confirmation",
            }
        if "outlet" in slot.slot_id:
            return {
                "value": "pressure_outlet",
                "unit": "Pa",
                "reason": "出口压力边界用于闭合压力场",
                "confidence": "high",
                "confirmation_policy": "recommend_and_notify",
                "status": "model_recommended",
            }
        if "wall" in slot.slot_id:
            return {
                "value": "no_slip",
                "reason": "默认壁面无滑移",
                "confidence": "high",
                "confirmation_policy": "none",
                "status": "model_recommended",
            }
        if "farfield" in slot.slot_id:
            return {
                "value": "freestream",
                "reason": "外流问题远场边界",
                "confidence": "high",
                "confirmation_policy": "recommend_and_notify",
            }
        if "lid" in slot.slot_id:
            return {
                "value": None,
                "reason": "需要用户指定盖板速度",
                "confidence": "low",
                "confirmation_policy": "blocking",
                "status": "unknown_required",
            }
        return None

    def _recommend_initial_condition(
        self, slot: RequiredSlot, spec: PhysicsSpecResult,
    ) -> dict | None:
        """Recommend initial condition."""
        if "velocity" in slot.slot_id:
            return {
                "value": "uniform (0 0 0)",
                "reason": "默认零初场",
                "status": "advanced_default",
            }
        if "pressure" in slot.slot_id:
            return {
                "value": "uniform 0",
                "unit": "Pa",
                "reason": "默认零压力初场",
                "status": "advanced_default",
            }
        if "turbulence" in slot.slot_id:
            return {
                "value": "uniform 0.01",
                "reason": "低湍流初场",
                "status": "advanced_default",
            }
        return None

    def _recommend_numerics(
        self, slot: RequiredSlot, spec: PhysicsSpecResult,
    ) -> dict | None:
        """Recommend numerics parameter."""
        short_key = slot.slot_id.split(".")[-1]
        if short_key == "time_step":
            if spec.temporal_type == "transient":
                return {
                    "value": 0.01,
                    "unit": "s",
                    "reason": "瞬态默认时间步长",
                    "confidence": "low",
                    "confirmation_policy": "require_confirmation",
                }
            return {
                "value": 1.0,
                "unit": "s",
                "reason": "稳态伪时间步",
                "confidence": "medium",
                "confirmation_policy": "recommend_and_notify",
            }
        if short_key == "end_time":
            if spec.temporal_type == "transient":
                return {
                    "value": 10.0,
                    "unit": "s",
                    "reason": "瞬态默认结束时间",
                    "confidence": "low",
                    "confirmation_policy": "require_confirmation",
                }
            return {
                "value": 1000.0,
                "unit": "s",
                "reason": "稳态默认迭代步数",
                "confidence": "medium",
                "confirmation_policy": "recommend_and_notify",
            }
        if short_key == "max_courant":
            return {
                "value": 1.0,
                "reason": "Courant 数上限推荐 1.0",
                "confidence": "high",
                "confirmation_policy": "recommend_and_notify",
            }
        return None

    def _recommend_mesh(
        self, slot: RequiredSlot, spec: PhysicsSpecResult,
    ) -> dict | None:
        """Recommend mesh parameter."""
        short_key = slot.slot_id.split(".")[-1]
        if short_key == "cells":
            return {
                "value": 50000,
                "reason": "默认网格规模",
                "confidence": "low",
                "status": "model_recommended",
            }
        if short_key in ("axial_cells", "cells_per_side"):
            return {
                "value": 50,
                "reason": "默认分辨率",
                "confidence": "medium",
                "status": "model_recommended",
            }
        if short_key in ("radial_cells", "cells_radial"):
            return {
                "value": 20,
                "reason": "默认径向分辨率",
                "confidence": "medium",
                "status": "model_recommended",
            }
        if short_key == "cells_wake":
            return {
                "value": 40,
                "reason": "尾迹加密默认值",
                "confidence": "medium",
                "status": "model_recommended",
            }
        return None
