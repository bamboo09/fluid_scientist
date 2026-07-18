"""PhysicsDependencyResolver — 参数依赖推导器.

在 missing-field 检查之前执行，自动推导所有可从已知量计算的参数。
每个推导结果都记录 formula 和 dependencies，标记为 FORMULA_DERIVED。

推导链：
  1. radius ↔ diameter:  D = 2R,  R = D/2
  2. characteristic_dimension = D
  3. nu = U * D / Re        (需要 U, D, Re)
  4. Re = U * D / nu        (需要 U, D, nu，当 Re 未给时)
  5. U = Re * nu / D        (需要 Re, nu, D，当 U 未给时)
  6. reference_length = D   (力系数参考长度)
  7. reference_area = D * 1 (二维单位展向参考面积)
  8. Strouhal number St     (经验公式，基于 Re)
  9. time step delta_t      (CFL 条件估算)

核心原则：
  - 推导必须在 missing-field 检查之前完成
  - 推导出的字段标记为 FORMULA_DERIVED，不向用户提问
  - 每个推导结果都带 formula 字符串（含实际数值）和 dependencies 列表
  - DerivationRecord 始终生成，即使 spec 字段已有值（用于文档和追溯）
  - 不覆盖更高优先级的值（USER_EXPLICIT, USER_CONFIRMED）
"""

from __future__ import annotations

import re
import math
from dataclasses import dataclass, field
from typing import Any

from fluid_scientist.cylinder_flow_2d.models import (
    CylinderFlow2DExperimentSpecV1,
    FieldSource,
    FieldStatus,
    ProvenanceField,
)


@dataclass
class DerivationRecord:
    """单次推导的完整记录。"""
    target_field: str
    value: float
    unit: str
    formula: str
    dependencies: list[str]
    source: str = "FORMULA_DERIVED"
    confidence: float = 1.0

    def to_display(self) -> str:
        deps_str = ", ".join(self.dependencies)
        return (
            f"{self.target_field} = {self.value} {self.unit} "
            f"(公式: {self.formula}; 依赖: {deps_str})"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_field": self.target_field,
            "value": self.value,
            "unit": self.unit,
            "formula": self.formula,
            "dependencies": list(self.dependencies),
            "source": self.source,
            "confidence": self.confidence,
        }


@dataclass
class DerivationResult:
    """推导器的完整输出。"""
    derivations: list[DerivationRecord] = field(default_factory=list)
    blocked_missing_fields: list[str] = field(default_factory=list)

    @property
    def derived_field_names(self) -> set[str]:
        return {d.target_field for d in self.derivations}

    def is_derived(self, field_name: str) -> bool:
        return field_name in self.derived_field_names

    def to_display_list(self) -> list[str]:
        return [d.to_display() for d in self.derivations]


class PhysicsDependencyResolver:
    """参数依赖推导器.

    在 missing-field 检查之前运行，自动推导所有可计算的参数。
    推导结果记录 formula 和 dependencies，标记为 FORMULA_DERIVED。

    关键设计：DerivationRecord 始终在可计算时生成，无论 spec 字段是否已有值。
    这确保了文档完整性和可追溯性，即使 resolver 被多次调用。
    """

    # 推导时假设的每直径网格数（用于 delta_t 估算）
    _DEFAULT_CELLS_PER_DIAMETER = 20

    def resolve(
        self,
        spec: CylinderFlow2DExperimentSpecV1,
    ) -> DerivationResult:
        """执行所有可能的参数推导。

        Returns:
            DerivationResult: 推导记录和被阻止的 missing-field 列表
        """
        result = DerivationResult()

        # --- 1. radius ↔ diameter ---
        self._derive_radius_diameter(spec, result)

        # --- 2. characteristic_dimension = D ---
        self._derive_characteristic_dimension(spec, result)

        # --- 3. Extract Reynolds number from user text ---
        re_val = self._extract_reynolds(spec.user_input_text or "")

        # --- 4. kinematic_viscosity = U * D / Re ---
        self._derive_viscosity(spec, result, re_val)

        # --- 5. Reynolds number = U * D / nu (when Re not given) ---
        if re_val is None:
            self._derive_reynolds(spec, result)
            # Re-try viscosity derivation with computed Re
            computed_re = self._find_derivation(result, "reynolds_number")
            if computed_re is not None:
                self._derive_viscosity(spec, result, computed_re)

        # --- 6. inlet velocity U = Re * nu / D (when U not given) ---
        self._derive_velocity(spec, result, re_val)

        # --- 7. reference_length = D ---
        self._derive_reference_length(spec, result)

        # --- 8. reference_area = D * 1 (2D unit span) ---
        self._derive_reference_area(spec, result)

        # --- 9. Strouhal number estimation ---
        self._derive_strouhal(spec, result, re_val)

        # --- 10. time step delta_t estimation ---
        self._derive_delta_t(spec, result)

        # --- 10b. Triangle obstacle dimension derivation ---
        self._derive_triangle_dimensions(spec, result)

        # --- 10c. Rectangle obstacle dimension derivation ---
        self._derive_rectangle_dimensions(spec, result)

        # --- 10d. Bottom profile (bump) center_x derivation ---
        self._derive_bump_center_x(spec, result)

        # --- 11. Block missing-field checks for derived fields ---
        for d in result.derivations:
            if d.target_field not in result.blocked_missing_fields:
                result.blocked_missing_fields.append(d.target_field)

        return result

    # ------------------------------------------------------------------
    # 推导方法
    # ------------------------------------------------------------------

    def _derive_radius_diameter(
        self,
        spec: CylinderFlow2DExperimentSpecV1,
        result: DerivationResult,
    ) -> None:
        """推导 radius ↔ diameter。

        始终生成 DerivationRecord（即使 spec 已有值），用于文档和追溯。
        只在 should_override 为 True 时更新 spec 字段。
        """
        radius_val = spec.cylinder.radius_m.value if spec.cylinder.radius_m.is_resolved() else None
        diameter_val = spec.cylinder.diameter_m.value if spec.cylinder.diameter_m.is_resolved() else None

        # D = 2R
        if radius_val is not None and radius_val > 0:
            d_computed = 2.0 * float(radius_val)
            # Always create DerivationRecord
            result.derivations.append(DerivationRecord(
                target_field="cylinder_diameter",
                value=d_computed,
                unit="m",
                formula=f"D = 2 * R  [R={radius_val} -> D={d_computed}]",
                dependencies=["cylinder_radius"],
            ))
            result.blocked_missing_fields.extend([
                "cylinder_diameter", "圆柱直径", "直径", "diameter",
            ])
            # Only update spec if should_override
            if diameter_val is None or FieldSource.should_override(
                spec.cylinder.diameter_m.source, FieldSource.FORMULA_DERIVED
            ):
                if diameter_val is None:
                    spec.cylinder.diameter_m = ProvenanceField(
                        value=d_computed,
                        source=FieldSource.FORMULA_DERIVED,
                        status=FieldStatus.RESOLVED,
                        confidence=1.0,
                        reason=f"D = 2R = 2 x {radius_val} = {d_computed} m",
                    )

        # R = D/2
        if diameter_val is not None and diameter_val > 0:
            r_computed = float(diameter_val) / 2.0
            # Always create DerivationRecord
            result.derivations.append(DerivationRecord(
                target_field="cylinder_radius",
                value=r_computed,
                unit="m",
                formula=f"R = D / 2  [D={diameter_val} -> R={r_computed}]",
                dependencies=["cylinder_diameter"],
            ))
            result.blocked_missing_fields.extend([
                "cylinder_radius", "圆柱半径", "半径", "radius",
            ])
            # Only update spec if should_override
            if radius_val is None or FieldSource.should_override(
                spec.cylinder.radius_m.source, FieldSource.FORMULA_DERIVED
            ):
                if radius_val is None:
                    spec.cylinder.radius_m = ProvenanceField(
                        value=r_computed,
                        source=FieldSource.FORMULA_DERIVED,
                        status=FieldStatus.RESOLVED,
                        confidence=1.0,
                        reason=f"R = D/2 = {diameter_val}/2 = {r_computed} m",
                    )

    def _derive_characteristic_dimension(
        self,
        spec: CylinderFlow2DExperimentSpecV1,
        result: DerivationResult,
    ) -> None:
        """推导 characteristic_dimension = D。

        始终生成 DerivationRecord，即使 spec 已有值。
        """
        d = spec.get_cylinder_diameter()
        if d is not None and d > 0:
            # Always create DerivationRecord
            result.derivations.append(DerivationRecord(
                target_field="characteristic_dimension",
                value=d,
                unit="m",
                formula=f"L_char = D  [D={d}]",
                dependencies=["cylinder_diameter"],
            ))
            result.blocked_missing_fields.extend([
                "characteristic_dimension", "特征尺度", "特征长度",
            ])
            # Only update spec if should_override
            if FieldSource.should_override(
                spec.cylinder.characteristic_dimension_m.source,
                FieldSource.FORMULA_DERIVED,
            ):
                spec.cylinder.characteristic_dimension_m = ProvenanceField(
                    value=d,
                    source=FieldSource.FORMULA_DERIVED,
                    status=FieldStatus.RESOLVED,
                    confidence=1.0,
                    reason=f"特征尺度 = 直径 = {d} m",
                )

    def _derive_viscosity(
        self,
        spec: CylinderFlow2DExperimentSpecV1,
        result: DerivationResult,
        re_val: float | None,
    ) -> None:
        """推导 nu = U * D / Re。

        始终生成 DerivationRecord，即使 spec 已有值。
        """
        if re_val is None or re_val <= 0:
            return

        u = spec.boundaries.left.inlet_velocity
        d = spec.get_cylinder_diameter()
        if u is None or d is None or u <= 0 or d <= 0:
            return

        nu = u * d / re_val

        # Always create DerivationRecord (even if spec already has nu)
        result.derivations.append(DerivationRecord(
            target_field="kinematic_viscosity",
            value=nu,
            unit="m^2/s",
            formula=f"nu = U * D / Re  [U={u}, D={d}, Re={re_val} -> nu={nu}]",
            dependencies=["inlet_velocity", "cylinder_diameter", "reynolds_number"],
        ))
        result.blocked_missing_fields.extend([
            "kinematic_viscosity", "运动黏度", "运动粘度", "nu", "viscosity",
        ])

        # Only update spec field if should_override
        if FieldSource.should_override(
            spec.fluid.kinematic_viscosity_m2_s.source,
            FieldSource.FORMULA_DERIVED,
        ):
            spec.fluid.kinematic_viscosity_m2_s = ProvenanceField(
                value=nu,
                source=FieldSource.FORMULA_DERIVED,
                status=FieldStatus.RESOLVED,
                confidence=1.0,
                reason=f"nu = U*D/Re = {u}*{d}/{re_val} = {nu} m^2/s",
            )

    def _derive_reynolds(
        self,
        spec: CylinderFlow2DExperimentSpecV1,
        result: DerivationResult,
    ) -> None:
        """推导 Re = U * D / nu（当 Re 未给但 U, D, nu 已知时）。

        重要：不使用 MODEL_RECOMMENDED 或 SYSTEM_DEFAULT 的 nu 来推导 Re，
        否则会造成循环推导（water default nu → 推导 Re → 再推导 nu → FORMULA_DERIVED）。
        只有当 nu 是用户提供的或已公式推导的，才允许推导 Re。
        """
        u = spec.boundaries.left.inlet_velocity
        d = spec.get_cylinder_diameter()
        nu_field = spec.fluid.kinematic_viscosity_m2_s
        nu = nu_field.value
        if u is None or d is None or nu is None or u <= 0 or d <= 0 or nu <= 0:
            return

        # 防止循环推导：不使用 MODEL_RECOMMENDED/SYSTEM_DEFAULT 的 nu 推导 Re
        if nu_field.source in (FieldSource.MODEL_RECOMMENDED, FieldSource.SYSTEM_DEFAULT):
            return

        re = u * d / nu

        # Always create DerivationRecord
        result.derivations.append(DerivationRecord(
            target_field="reynolds_number",
            value=re,
            unit="",
            formula=f"Re = U * D / nu  [U={u}, D={d}, nu={nu} -> Re={re}]",
            dependencies=["inlet_velocity", "cylinder_diameter", "kinematic_viscosity"],
        ))
        result.blocked_missing_fields.extend([
            "reynolds_number", "雷诺数", "Re",
        ])

    def _derive_velocity(
        self,
        spec: CylinderFlow2DExperimentSpecV1,
        result: DerivationResult,
        re_val: float | None,
    ) -> None:
        """推导 U = Re * nu / D（当 Re, nu, D 已知时）。

        这是新增的推导链，填补了"用户给 Re 和 nu 但没给速度"的场景。
        始终生成 DerivationRecord，即使 spec 已有值（用于文档和追溯）。
        但不覆盖用户明确提供的值。
        """
        # If U is user-provided, skip derivation
        u_current = spec.boundaries.left.inlet_velocity
        u_source = spec.boundaries.left.source
        if (u_current is not None and u_current > 0
                and u_source in (FieldSource.USER_EXPLICIT, FieldSource.USER_CONFIRMED)):
            return  # U is user-provided, no need to derive

        # Try to get Re: from text or from derivation result
        re_use = re_val
        if re_use is None:
            re_use = self._find_derivation(result, "reynolds_number")
        if re_use is None or re_use <= 0:
            return

        d = spec.get_cylinder_diameter()
        nu = spec.fluid.kinematic_viscosity_m2_s.value
        if d is None or nu is None or d <= 0 or nu <= 0:
            return

        u_computed = re_use * nu / d

        # Always create DerivationRecord (even if spec already has formula-derived U)
        result.derivations.append(DerivationRecord(
            target_field="inlet_velocity",
            value=u_computed,
            unit="m/s",
            formula=f"U = Re * nu / D  [Re={re_use}, nu={nu}, D={d} -> U={u_computed}]",
            dependencies=["reynolds_number", "kinematic_viscosity", "cylinder_diameter"],
        ))
        result.blocked_missing_fields.extend([
            "inlet_velocity", "入口速度", "来流速度", "velocity",
        ])

        # Only update spec if U is not set or should_override
        if u_current is None or FieldSource.should_override(
            u_source, FieldSource.FORMULA_DERIVED
        ):
            if u_current is None:
                spec.boundaries.left.inlet_velocity = u_computed
                spec.boundaries.left.source = FieldSource.FORMULA_DERIVED
                spec.boundaries.left.status = FieldStatus.RESOLVED

    def _derive_reference_length(
        self,
        spec: CylinderFlow2DExperimentSpecV1,
        result: DerivationResult,
    ) -> None:
        """推导 reference_length = D (力系数参考长度)。"""
        d = spec.get_cylinder_diameter()
        if d is not None and d > 0:
            result.derivations.append(DerivationRecord(
                target_field="reference_length",
                value=d,
                unit="m",
                formula=f"L_ref = D  [D={d}]",
                dependencies=["cylinder_diameter"],
            ))
            result.blocked_missing_fields.extend([
                "reference_length", "参考长度", "力系数参考长度",
            ])

    def _derive_reference_area(
        self,
        spec: CylinderFlow2DExperimentSpecV1,
        result: DerivationResult,
    ) -> None:
        """推导 reference_area = D * 1 (二维单位展向参考面积)。"""
        d = spec.get_cylinder_diameter()
        if d is not None and d > 0:
            area = d * 1.0  # 2D, unit span
            result.derivations.append(DerivationRecord(
                target_field="reference_area",
                value=area,
                unit="m^2",
                formula=f"A_ref = D * 1 (2D unit span)  [D={d} -> A_ref={area}]",
                dependencies=["cylinder_diameter"],
            ))
            result.blocked_missing_fields.extend([
                "reference_area", "参考面积",
            ])

    def _derive_strouhal(
        self,
        spec: CylinderFlow2DExperimentSpecV1,
        result: DerivationResult,
        re_val: float | None,
    ) -> None:
        """估算 Strouhal 数。

        经验公式：
          Re < 40:    St = 0 （定常流，无涡脱落）
          40 <= Re < 250: St = 0.198 * (1 - 19.7/Re)
          Re >= 250:  St ≈ 0.21

        同时推导涡脱落频率 f = St * U / D。
        """
        # Get Re: from text, from derivation, or from spec estimate
        re_use = re_val
        if re_use is None:
            re_use = self._find_derivation(result, "reynolds_number")
        if re_use is None:
            re_use = spec.estimate_reynolds()
        if re_use is None or re_use <= 0:
            return

        # Compute Strouhal number
        if re_use < 40:
            st = 0.0
            formula_str = f"St = 0  (Re={re_use} < 40, steady flow, no shedding)"
        elif re_use < 250:
            st = 0.198 * (1.0 - 19.7 / re_use)
            formula_str = (
                f"St = 0.198 * (1 - 19.7/Re)  "
                f"[Re={re_use} -> St={st}]"
            )
        else:
            st = 0.21
            formula_str = f"St = 0.21  (Re={re_use} >= 250, asymptotic)"

        result.derivations.append(DerivationRecord(
            target_field="strouhal_number",
            value=st,
            unit="",
            formula=formula_str,
            dependencies=["reynolds_number"],
            confidence=0.85,
        ))
        result.blocked_missing_fields.extend([
            "strouhal_number", "Strouhal数", "斯特劳哈尔数",
        ])

        # Also derive shedding frequency f = St * U / D
        u = spec.boundaries.left.inlet_velocity
        d = spec.get_cylinder_diameter()
        if u is not None and d is not None and u > 0 and d > 0 and st > 0:
            f_shed = st * u / d
            result.derivations.append(DerivationRecord(
                target_field="shedding_frequency",
                value=f_shed,
                unit="Hz",
                formula=f"f = St * U / D  [St={st}, U={u}, D={d} -> f={f_shed}]",
                dependencies=["strouhal_number", "inlet_velocity", "cylinder_diameter"],
                confidence=0.8,
            ))
            result.blocked_missing_fields.extend([
                "shedding_frequency", "涡脱落频率",
            ])

    def _derive_delta_t(
        self,
        spec: CylinderFlow2DExperimentSpecV1,
        result: DerivationResult,
    ) -> None:
        """估算时间步长 delta_t。

        基于 CFL 条件: delta_t = CFL * D / (U * N_cells)
        其中 N_cells 是每直径网格数（默认 20）。
        """
        u = spec.boundaries.left.inlet_velocity
        d = spec.get_cylinder_diameter()
        if u is None or d is None or u <= 0 or d <= 0:
            return

        cfl = spec.simulation.max_courant_number
        n_cells = self._DEFAULT_CELLS_PER_DIAMETER
        dt = cfl * d / (u * n_cells)

        result.derivations.append(DerivationRecord(
            target_field="time_step",
            value=dt,
            unit="s",
            formula=(
                f"delta_t = CFL * D / (U * N_cells)  "
                f"[CFL={cfl}, D={d}, U={u}, N={n_cells} -> dt={dt}]"
            ),
            dependencies=["cylinder_diameter", "inlet_velocity", "max_courant_number"],
            confidence=0.7,
        ))
        result.blocked_missing_fields.extend([
            "time_step", "delta_t", "时间步长",
        ])

    def _derive_triangle_dimensions(
        self,
        spec: CylinderFlow2DExperimentSpecV1,
        result: DerivationResult,
    ) -> None:
        """推导三角形障碍物的尺寸默认值。

        当用户提到三角形但未给出具体尺寸时，根据圆柱直径推导合理默认值：
        - base_width = 2 * D（底宽等于圆柱直径的2倍）
        - height = D（高度等于圆柱直径）
        - center_x = 圆柱圆心x坐标（如果未指定）
        - apex_direction = "up"（默认朝上）
        - attached_boundary = "bottom_wall"（默认贴附底面）
        """
        if not spec.triangle.enabled:
            return

        # Get cylinder diameter for scaling
        cyl_d = spec.get_cylinder_diameter()
        cyl_d_val = cyl_d if cyl_d is not None and cyl_d > 0 else 0.2  # fallback 0.2m

        # Derive base_width if missing
        if not spec.triangle.base_width_m.is_resolved():
            default_bw = 2.0 * cyl_d_val
            result.derivations.append(DerivationRecord(
                target_field="triangle_base_width",
                value=default_bw,
                unit="m",
                formula=f"2 * D [D={cyl_d_val} -> bw={default_bw}]",
                dependencies=["cylinder_diameter"],
                confidence=0.6,
            ))
            spec.triangle.base_width_m = ProvenanceField(
                value=default_bw,
                source=FieldSource.FORMULA_DERIVED,
                status=FieldStatus.RESOLVED,
                confidence=0.6,
                reason=f"默认底宽 = 2D = {default_bw}m（用户未指定，根据圆柱直径推导）",
            )
            result.blocked_missing_fields.append("triangle_base_width")

        # Derive height if missing
        if not spec.triangle.height_m.is_resolved():
            default_h = cyl_d_val
            result.derivations.append(DerivationRecord(
                target_field="triangle_height",
                value=default_h,
                unit="m",
                formula=f"D [D={cyl_d_val} -> h={default_h}]",
                dependencies=["cylinder_diameter"],
                confidence=0.6,
            ))
            spec.triangle.height_m = ProvenanceField(
                value=default_h,
                source=FieldSource.FORMULA_DERIVED,
                status=FieldStatus.RESOLVED,
                confidence=0.6,
                reason=f"默认高度 = D = {default_h}m（用户未指定，根据圆柱直径推导）",
            )
            result.blocked_missing_fields.append("triangle_height")

        # Derive center_x if missing — use cylinder center_x
        if not spec.triangle.center_x_m.is_resolved() and spec.has_cylinder:
            cyl_cx = spec.cylinder.center_x_m.value
            if cyl_cx is not None:
                result.derivations.append(DerivationRecord(
                    target_field="triangle_center_x",
                    value=float(cyl_cx),
                    unit="m",
                    formula=f"cylinder_center_x [{cyl_cx}]",
                    dependencies=["cylinder_center_x"],
                    confidence=0.7,
                ))
                spec.triangle.center_x_m = ProvenanceField(
                    value=float(cyl_cx),
                    source=FieldSource.FORMULA_DERIVED,
                    status=FieldStatus.RESOLVED,
                    confidence=0.7,
                    reason=f"默认位置 = 圆柱圆心x = {cyl_cx}m",
                )
                result.blocked_missing_fields.append("triangle_center_x")

    def _derive_rectangle_dimensions(
        self,
        spec: CylinderFlow2DExperimentSpecV1,
        result: DerivationResult,
    ) -> None:
        """推导矩形障碍物的尺寸默认值。

        当用户提到矩形但未给出具体尺寸时，根据圆柱直径推导合理默认值：
        - width = 4 * D（宽度等于圆柱直径的4倍）
        - height = 2 * D（高度等于圆柱直径的2倍）
        - center_x = 圆柱圆心x坐标（如果未指定且对齐圆柱下方）
        - center_y = height / 2（贴附底面）
        """
        if not spec.rectangle.enabled:
            return

        # Get cylinder diameter for scaling
        cyl_d = spec.get_cylinder_diameter()
        cyl_d_val = cyl_d if cyl_d is not None and cyl_d > 0 else 0.2  # fallback 0.2m

        # Derive width if missing
        if not spec.rectangle.width_m.is_resolved():
            default_w = 4.0 * cyl_d_val
            result.derivations.append(DerivationRecord(
                target_field="rectangle_width",
                value=default_w,
                unit="m",
                formula=f"4 * D [D={cyl_d_val} -> w={default_w}]",
                dependencies=["cylinder_diameter"],
                confidence=0.6,
            ))
            spec.rectangle.width_m = ProvenanceField(
                value=default_w,
                source=FieldSource.FORMULA_DERIVED,
                status=FieldStatus.RESOLVED,
                confidence=0.6,
                reason=f"默认宽度 = 4D = {default_w}m（用户未指定，根据圆柱直径推导）",
            )
            result.blocked_missing_fields.append("rectangle_width")

        # Derive height if missing
        if not spec.rectangle.height_m.is_resolved():
            default_h = 2.0 * cyl_d_val
            result.derivations.append(DerivationRecord(
                target_field="rectangle_height",
                value=default_h,
                unit="m",
                formula=f"2 * D [D={cyl_d_val} -> h={default_h}]",
                dependencies=["cylinder_diameter"],
                confidence=0.6,
            ))
            spec.rectangle.height_m = ProvenanceField(
                value=default_h,
                source=FieldSource.FORMULA_DERIVED,
                status=FieldStatus.RESOLVED,
                confidence=0.6,
                reason=f"默认高度 = 2D = {default_h}m（用户未指定，根据圆柱直径推导）",
            )
            result.blocked_missing_fields.append("rectangle_height")

        # Derive center_x if missing — use cylinder center_x
        if not spec.rectangle.center_x_m.is_resolved() and spec.has_cylinder:
            cyl_cx = spec.cylinder.center_x_m.value
            if cyl_cx is not None:
                result.derivations.append(DerivationRecord(
                    target_field="rectangle_center_x",
                    value=float(cyl_cx),
                    unit="m",
                    formula=f"cylinder_center_x [{cyl_cx}]",
                    dependencies=["cylinder_center_x"],
                    confidence=0.7,
                ))
                spec.rectangle.center_x_m = ProvenanceField(
                    value=float(cyl_cx),
                    source=FieldSource.FORMULA_DERIVED,
                    status=FieldStatus.RESOLVED,
                    confidence=0.7,
                    reason=f"默认位置 = 圆柱圆心x = {cyl_cx}m",
                )
                result.blocked_missing_fields.append("rectangle_center_x")

    def _derive_bump_center_x(
        self,
        spec: CylinderFlow2DExperimentSpecV1,
        result: DerivationResult,
    ) -> None:
        """推导底面凸起(bump)的中心x坐标。

        当用户说"位于圆柱正下方"但未给出具体x坐标时，
        从圆柱圆心x坐标推导凸起中心x坐标。
        """
        if not spec.bottom_profile.enabled:
            return
        if spec.bottom_profile.center_x_m.is_resolved():
            return  # Already set by user or pipeline
        if not getattr(spec.bottom_profile, "aligned_below_cylinder", False):
            return  # No alignment info
        if not spec.has_cylinder:
            return  # No cylinder to align with

        cyl_cx = spec.cylinder.center_x_m.value
        if cyl_cx is None:
            return

        result.derivations.append(DerivationRecord(
            target_field="bump_center_x",
            value=float(cyl_cx),
            unit="m",
            formula=f"cylinder_center_x [{cyl_cx}]",
            dependencies=["cylinder_center_x"],
            confidence=0.8,
        ))
        spec.bottom_profile.center_x_m = ProvenanceField(
            value=float(cyl_cx),
            source=FieldSource.FORMULA_DERIVED,
            status=FieldStatus.RESOLVED,
            confidence=0.8,
            reason=f"用户指定'位于圆柱正下方'，凸起中心x = 圆柱圆心x = {cyl_cx}m",
        )
        result.blocked_missing_fields.append("bump_center_x")

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    def _find_derivation(
        self, result: DerivationResult, target_field: str
    ) -> float | None:
        """从已有的推导结果中查找某个字段的推导值。"""
        for d in result.derivations:
            if d.target_field == target_field:
                return d.value
        return None

    def _extract_reynolds(self, text: str) -> float | None:
        """从用户文本中提取 Reynolds 数。"""
        _NUM = r"(\d+\.?\d*(?:[eE][+-]?\d+)?)"
        patterns = [
            rf"(?<![a-zA-Z])[Rr]e\s*(?:保持|设为|设置为|为|是|=)?\s*{_NUM}",
            rf"[Rr]eynolds\s*(?:number)?\s*(?:保持|设为|设置为|为|是|=|:)?\s*{_NUM}",
            rf"雷诺数\s*(?:保持|设为|设置为|为|是|:|=)?\s*{_NUM}",
            rf"(?<![a-zA-Z])[Rr]e\s+{_NUM}",  # "Re 200" with space
        ]
        for p in patterns:
            m = re.search(p, text, re.IGNORECASE)
            if m:
                val = float(m.group(1))
                if 0.1 < val < 1e8:
                    return val
        return None
