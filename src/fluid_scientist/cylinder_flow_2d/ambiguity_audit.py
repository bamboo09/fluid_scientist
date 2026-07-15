"""AmbiguityAndConflictAuditor — 冲突和歧义检测审计器.

将检测到的问题分为五类：

  BLOCKING_CONFLICT        — 硬冲突，必须阻断，用户必须解决
  SOLVER_CRITICAL_AMBIGUITY — 求解关键歧义，必须询问用户
  NON_BLOCKING_ASSUMPTION  — 低风险默认假设，不阻断但必须展示
  DERIVED_VALUE            — 可推导参数，展示推导过程，不询问
  TRUE_MISSING_FIELD       — 真正缺失的字段，必须询问

核心原则：
  - 推导值（DERIVED_VALUE）永远不向用户提问
  - 非阻断假设（NON_BLOCKING_ASSUMPTION）在确认页面展示，不阻止流程
  - 只有 BLOCKING_CONFLICT 和 SOLVER_CRITICAL_AMBIGUITY 和 TRUE_MISSING_FIELD 阻断

新增检测：
  - Re vs nu 物理一致性冲突
  - 圆柱与边界几何可行性检查（集成 skill pack 逻辑）
  - 三角形与圆柱位置重叠检查
  - 稳态 vs 涡脱落时间模式冲突
"""

from __future__ import annotations

import re
import math
from dataclasses import dataclass, field
from typing import Any
from enum import Enum

from fluid_scientist.cylinder_flow_2d.models import (
    CylinderFlow2DExperimentSpecV1,
    FieldSource,
    FieldStatus,
    ProvenanceField,
    SemanticBoundaryType,
    BumpProfileType,
)


class IssueCategory(str, Enum):
    """问题分类。"""

    BLOCKING_CONFLICT = "BLOCKING_CONFLICT"
    SOLVER_CRITICAL_AMBIGUITY = "SOLVER_CRITICAL_AMBIGUITY"
    NON_BLOCKING_ASSUMPTION = "NON_BLOCKING_ASSUMPTION"
    DERIVED_VALUE = "DERIVED_VALUE"
    TRUE_MISSING_FIELD = "TRUE_MISSING_FIELD"


@dataclass
class AuditIssue:
    """审计发现的问题。"""

    category: IssueCategory
    code: str
    title: str
    description: str
    user_evidence: str = ""
    recommendation: str = ""
    options: list[str] = field(default_factory=list)
    derivations: list[dict] = field(default_factory=list)
    blocks: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category.value,
            "code": self.code,
            "title": self.title,
            "description": self.description,
            "user_evidence": self.user_evidence,
            "recommendation": self.recommendation,
            "options": list(self.options),
            "derivations": list(self.derivations),
            "blocks": self.blocks,
        }


@dataclass
class AuditResult:
    """审计器的完整输出。"""

    issues: list[AuditIssue] = field(default_factory=list)

    @property
    def blocking_issues(self) -> list[AuditIssue]:
        return [i for i in self.issues if i.blocks]

    @property
    def non_blocking_issues(self) -> list[AuditIssue]:
        return [i for i in self.issues if not i.blocks]

    @property
    def derived_values(self) -> list[AuditIssue]:
        return [i for i in self.issues if i.category == IssueCategory.DERIVED_VALUE]

    @property
    def assumptions(self) -> list[AuditIssue]:
        return [i for i in self.issues if i.category == IssueCategory.NON_BLOCKING_ASSUMPTION]

    def to_dict_list(self) -> list[dict]:
        return [i.to_dict() for i in self.issues]


class AmbiguityAndConflictAuditor:
    """冲突和歧义检测审计器.

    检测用户输入中的：
    1. 位置冲突（如"距下壁面2m" vs "正中央"）
    2. 边界条件歧义（如"自由出流"）
    3. Reynolds 特征长度歧义（D vs R）
    4. 三角形方向假设
    5. 可推导参数
    6. 真正缺失的字段
    7. Re vs nu 物理一致性冲突
    8. 圆柱与边界几何可行性检查
    9. 三角形与圆柱位置重叠检查
    10. 稳态 vs 涡脱落时间模式冲突
    """

    # Re vs nu 一致性容差（10%）
    _RE_NU_TOLERANCE = 0.10

    def audit(
        self,
        spec: CylinderFlow2DExperimentSpecV1,
        user_text: str,
        derivation_result: Any = None,
    ) -> AuditResult:
        """执行完整审计。

        Args:
            spec: 当前实验规格
            user_text: 用户原始输入文本
            derivation_result: PhysicsDependencyResolver 的输出

        Returns:
            AuditResult: 包含所有检测到的问题
        """
        result = AuditResult()

        # 1. 位置冲突检测
        self._check_position_conflicts(spec, user_text, result)

        # 2. 边界条件歧义
        self._check_boundary_ambiguities(spec, user_text, result)

        # 3. Reynolds 特征长度歧义
        self._check_reynolds_characteristic_length(spec, user_text, result)

        # 4. 三角形非阻断假设
        self._check_triangle_assumptions(spec, user_text, result)

        # 5. 圆柱位置非阻断假设
        self._check_cylinder_position_assumptions(spec, user_text, result)

        # 6. Re vs nu 物理一致性冲突
        self._check_re_nu_consistency(spec, user_text, result)

        # 7. 几何可行性检查（圆柱与边界）
        self._check_geometry_feasibility(spec, user_text, result)

        # 8. 三角形与圆柱重叠检查
        self._check_triangle_cylinder_overlap(spec, user_text, result)

        # 9. 时间模式冲突（稳态 vs 涡脱落）
        self._check_time_mode_conflict(spec, user_text, result)

        # 10. 推导值展示
        if derivation_result is not None:
            self._collect_derived_values(derivation_result, result)

        # 11. 真正缺失的字段
        self._check_truly_missing(spec, user_text, result, derivation_result)

        return result

    # ------------------------------------------------------------------
    # BLOCKING_CONFLICT
    # ------------------------------------------------------------------

    def _check_position_conflicts(
        self,
        spec: CylinderFlow2DExperimentSpecV1,
        user_text: str,
        result: AuditResult,
    ) -> None:
        """检测位置冲突。"""
        # 圆柱 Y 位置冲突："距下壁面Xm" vs "正中央"
        wall_dist_match = re.search(r'圆心距.*?壁面\s*([\d.]+)\s*m', user_text)
        has_center = (
            "正中央" in user_text
            or "几何正中央" in user_text
            or "几何中心" in user_text
        )

        if wall_dist_match and has_center:
            wall_y = float(wall_dist_match.group(1))
            domain_h = spec.domain.height_m.value
            if domain_h and domain_h > 0:
                center_y = domain_h / 2.0
                if abs(wall_y - center_y) > 0.01:
                    result.issues.append(AuditIssue(
                        category=IssueCategory.BLOCKING_CONFLICT,
                        code="CYLINDER_Y_POSITION_CONFLICT",
                        title="圆柱纵向位置冲突",
                        description=(
                            f"流场高度为 {domain_h} m。"
                            f"'圆心距下壁面 {wall_y} m' 对应圆心 y={wall_y} m；"
                            f"'位于流场正中央' 对应圆心 y={center_y} m。"
                            f"两者不能同时成立。"
                        ),
                        user_evidence=wall_dist_match.group(0),
                        recommendation=(
                            f"保留'距下壁面 {wall_y} m'这一精确数值，"
                            f"将'正中央'解释为仅在水平方向居中。"
                        ),
                        options=[
                            f"圆心设为 ({spec.cylinder.center_x_m.value or 'X'}, {wall_y}) m，水平方向居中",
                            f"圆心设为 ({spec.cylinder.center_x_m.value or 'X'}, {center_y}) m，几何中心",
                            "自定义位置",
                        ],
                        blocks=True,
                    ))

        # 圆柱 X 位置冲突：如同时给出"x=5m"和"距入口3m"（且域长不同）
        x_explicit = re.search(r'[xX]\s*[=为在]\s*(\d+\.?\d*)\s*m', user_text)
        x_from_inlet = re.search(r'距.*?入口\s*(\d+\.?\d*)\s*m', user_text)
        if x_explicit and x_from_inlet:
            x1 = float(x_explicit.group(1))
            x2 = float(x_from_inlet.group(1))
            if abs(x1 - x2) > 0.01:
                result.issues.append(AuditIssue(
                    category=IssueCategory.BLOCKING_CONFLICT,
                    code="CYLINDER_X_POSITION_CONFLICT",
                    title="圆柱水平位置冲突",
                    description=(
                        f"'x={x1} m' 与 '距入口 {x2} m' 给出不同的水平位置。"
                    ),
                    options=[
                        f"使用 x={x1} m",
                        f"使用距入口 {x2} m",
                        "自定义位置",
                    ],
                    blocks=True,
                ))

    def _check_re_nu_consistency(
        self,
        spec: CylinderFlow2DExperimentSpecV1,
        user_text: str,
        result: AuditResult,
    ) -> None:
        """检测 Re 与 nu 的物理一致性冲突。

        如果用户同时给出了 Re 和 nu，则可以验证：
        nu_expected = U * D / Re
        如果 nu_given 与 nu_expected 偏差超过容差，则标记为冲突。
        """
        # Extract Re from text
        re_match = re.search(r'(?<![a-zA-Z])[Rr]e\s*=\s*(\d+\.?\d*)', user_text)
        if re_match is None:
            re_match = re.search(r'雷诺数\s*[=为]?\s*(\d+\.?\d*)', user_text)
        if re_match is None:
            return  # No Re given, can't check

        re_val = float(re_match.group(1))

        # Check if nu was user-explicit (not derived or defaulted)
        nu_field = spec.fluid.kinematic_viscosity_m2_s
        if not nu_field.is_user_provided():
            return  # nu is not user-given, no conflict possible

        nu_given = nu_field.value
        if nu_given is None or nu_given <= 0:
            return

        u = spec.boundaries.left.inlet_velocity
        d = spec.get_cylinder_diameter()
        if u is None or d is None or u <= 0 or d <= 0:
            return  # Can't compute expected nu without U and D

        nu_expected = u * d / re_val
        relative_error = abs(nu_given - nu_expected) / nu_expected

        if relative_error > self._RE_NU_TOLERANCE:
            result.issues.append(AuditIssue(
                category=IssueCategory.BLOCKING_CONFLICT,
                code="RE_NU_INCONSISTENCY",
                title="Reynolds 数与运动粘度物理不一致",
                description=(
                    f"用户给出的参数存在物理不一致：\n"
                    f"  Re = {re_val}\n"
                    f"  U = {u} m/s, D = {d} m\n"
                    f"  由 Re = U*D/nu 推算：nu_expected = {nu_expected:.6e} m^2/s\n"
                    f"  用户给出的 nu = {nu_given:.6e} m^2/s\n"
                    f"  相对偏差 = {relative_error*100:.1f}%（容差 {self._RE_NU_TOLERANCE*100:.0f}%）\n"
                    f"  两者不能同时成立。"
                ),
                user_evidence=f"Re={re_val}, nu={nu_given}",
                recommendation=(
                    f"保留 Re={re_val}，将 nu 修正为 {nu_expected:.6e} m^2/s（由 U*D/Re 推导）"
                ),
                options=[
                    f"保留 Re={re_val}，nu 改为 {nu_expected:.6e} m^2/s（推荐）",
                    f"保留 nu={nu_given}，Re 改为 {u*d/nu_given:.1f}",
                    "自定义参数",
                ],
                blocks=True,
            ))

    def _check_geometry_feasibility(
        self,
        spec: CylinderFlow2DExperimentSpecV1,
        user_text: str,
        result: AuditResult,
    ) -> None:
        """检测圆柱与域边界的几何可行性。

        集成 skill pack 的 validate_geometry_feasibility 逻辑。
        """
        # Only check if we have enough information
        domain_l = spec.domain.length_m.value
        domain_h = spec.domain.height_m.value
        cx = spec.cylinder.center_x_m.value
        cy = spec.cylinder.center_y_m.value
        radius = spec.get_cylinder_radius()

        if any(v is None for v in [domain_l, domain_h, cx, cy, radius]):
            return

        if radius <= 0:
            return

        # Check side boundaries
        if cx - radius <= 0 or cx + radius >= domain_l:
            result.issues.append(AuditIssue(
                category=IssueCategory.BLOCKING_CONFLICT,
                code="CYLINDER_INTERSECTS_SIDE_BOUNDARY",
                title="圆柱与左右边界相交",
                description=(
                    f"圆柱圆心 ({cx}, {cy}) m，半径 {radius} m。"
                    f"域范围 [0, {domain_l}] x [0, {domain_h}] m。"
                    f"圆柱与左右边界相交或距离过小。"
                ),
                recommendation=f"调整圆心 x 坐标到 ({radius + 0.1*radius:.2f}, {domain_l - radius - 0.1*radius:.2f}) 范围内",
                options=[
                    f"将圆心 x 移至 {domain_l / 2.0:.1f} m（域中心）",
                    "自定义位置",
                ],
                blocks=True,
            ))

        # Check bottom boundary
        if cy - radius <= 0:
            result.issues.append(AuditIssue(
                category=IssueCategory.BLOCKING_CONFLICT,
                code="CYLINDER_INTERSECTS_BOTTOM",
                title="圆柱与下边界相交",
                description=(
                    f"圆柱圆心 ({cx}, {cy}) m，半径 {radius} m。"
                    f"圆柱底部 y={cy - radius:.3f} m 穿入下边界。"
                ),
                recommendation=f"调整圆心 y 到 {radius + 0.1*radius:.2f} m 以上",
                options=[
                    f"将圆心 y 移至 {radius + max(radius, 0.5):.2f} m",
                    "自定义位置",
                ],
                blocks=True,
            ))

        # Check top boundary
        if cy + radius >= domain_h:
            result.issues.append(AuditIssue(
                category=IssueCategory.BLOCKING_CONFLICT,
                code="CYLINDER_INTERSECTS_TOP",
                title="圆柱与上边界相交",
                description=(
                    f"圆柱圆心 ({cx}, {cy}) m，半径 {radius} m。"
                    f"圆柱顶部 y={cy + radius:.3f} m 超出上边界 {domain_h} m。"
                ),
                recommendation=f"调整圆心 y 到 {domain_h - radius - 0.1*radius:.2f} m 以下",
                options=[
                    f"将圆心 y 移至 {domain_h / 2.0:.1f} m（域中心）",
                    "自定义位置",
                ],
                blocks=True,
            ))

    def _check_triangle_cylinder_overlap(
        self,
        spec: CylinderFlow2DExperimentSpecV1,
        user_text: str,
        result: AuditResult,
    ) -> None:
        """检测三角形与圆柱的位置重叠。

        三角形底边贴附下壁面（y=0），顶点在 y=triangle_height。
        圆柱在 (cylinder_x, cylinder_y)，半径 r。
        如果圆柱底部 (cylinder_y - r) 低于三角形顶点，且 x 范围重叠，则可能碰撞。
        """
        if not spec.has_triangle or not spec.has_cylinder:
            return

        tri_h = spec.triangle.height_m.value
        tri_cx = spec.triangle.center_x_m.value
        tri_bw = spec.triangle.base_width_m.value

        cyl_cx = spec.cylinder.center_x_m.value
        cyl_cy = spec.cylinder.center_y_m.value
        cyl_r = spec.get_cylinder_radius()

        if any(v is None for v in [tri_h, tri_cx, tri_bw, cyl_cx, cyl_cy, cyl_r]):
            return

        # Check if x ranges overlap
        tri_x_min = tri_cx - tri_bw / 2.0
        tri_x_max = tri_cx + tri_bw / 2.0
        cyl_x_min = cyl_cx - cyl_r
        cyl_x_max = cyl_cx + cyl_r

        x_overlap = not (tri_x_max < cyl_x_min or cyl_x_min > cyl_x_max)

        if not x_overlap:
            return  # No x overlap, no collision possible

        # Check if cylinder bottom is below triangle apex
        cyl_bottom = cyl_cy - cyl_r
        if cyl_bottom < tri_h:
            result.issues.append(AuditIssue(
                category=IssueCategory.BLOCKING_CONFLICT,
                code="TRIANGLE_CYLINDER_OVERLAP",
                title="三角形与圆柱位置重叠",
                description=(
                    f"三角形底宽 {tri_bw} m，高 {tri_h} m，位于 x={tri_cx}。"
                    f"圆柱圆心 ({cyl_cx}, {cyl_cy}) m，半径 {cyl_r} m。"
                    f"圆柱底部 y={cyl_bottom:.3f} m 低于三角形顶点 y={tri_h} m，"
                    f"且两者 x 范围重叠，存在几何碰撞。"
                ),
                recommendation=(
                    f"将三角形移至远离圆柱的位置，或将圆柱上移至 y > {tri_h + cyl_r:.2f} m"
                ),
                options=[
                    f"将三角形移至圆柱上游（x < {cyl_x_min - tri_bw:.2f} m）",
                    f"将圆柱上移至 y = {tri_h + cyl_r + 0.5:.2f} m",
                    "自定义位置",
                ],
                blocks=True,
            ))

    def _check_time_mode_conflict(
        self,
        spec: CylinderFlow2DExperimentSpecV1,
        user_text: str,
        result: AuditResult,
    ) -> None:
        """检测时间模式冲突：稳态 vs 涡脱落。

        涡脱落 (vortex shedding) 是非定常现象，不能用稳态求解器捕捉。
        如果用户同时要求"稳态"和"涡脱落"，这是硬冲突。
        """
        has_steady = (
            "稳态" in user_text
            or "steady" in user_text.lower()
            or "定常" in user_text
        )
        has_shedding = (
            "涡脱落" in user_text
            or "涡街" in user_text
            or "vortex shedding" in user_text.lower()
            or "Karman" in user_text
            or "卡门" in user_text
        )

        if has_steady and has_shedding:
            result.issues.append(AuditIssue(
                category=IssueCategory.BLOCKING_CONFLICT,
                code="TIME_MODE_CONFLICT",
                title="时间模式冲突：稳态与涡脱落不兼容",
                description=(
                    "用户同时要求'稳态计算'和'观察涡脱落'，但这两者在物理上不兼容。\n"
                    "涡脱落（von Karman 涡街）是非定常现象，需要瞬态求解才能捕捉。\n"
                    "稳态求解器会收敛到定常解，无法产生周期性涡脱落。"
                ),
                user_evidence="稳态 + 涡脱落",
                recommendation="使用瞬态（transient）求解器以捕捉涡脱落现象",
                options=[
                    "改为瞬态计算，捕捉涡脱落（推荐）",
                    "保持稳态计算，放弃涡脱落观测",
                    "自定义",
                ],
                blocks=True,
            ))

    # ------------------------------------------------------------------
    # SOLVER_CRITICAL_AMBIGUITY
    # ------------------------------------------------------------------

    def _check_boundary_ambiguities(
        self,
        spec: CylinderFlow2DExperimentSpecV1,
        user_text: str,
        result: AuditResult,
    ) -> None:
        """检测边界条件歧义。"""
        # 上边界"自由出流"
        if "自由出流" in user_text or "自由出口" in user_text:
            if "顶" in user_text or "上" in user_text:
                top_bc = spec.boundaries.top.semantic_type
                # 检查是否已被解析为具体类型
                if top_bc in (SemanticBoundaryType.OPEN_BOUNDARY, None):
                    result.issues.append(AuditIssue(
                        category=IssueCategory.SOLVER_CRITICAL_AMBIGUITY,
                        code="TOP_BOUNDARY_AMBIGUITY",
                        title="上边界'自由出流'歧义",
                        description=(
                            "'自由出流'不能唯一映射为 OpenFOAM 边界条件。"
                            "对于水平来流外流场，推荐使用 symmetryPlane/slip 作为远场边界。"
                        ),
                        user_evidence="上自由出流",
                        recommendation="symmetryPlane / slip（推荐）",
                        options=[
                            "symmetryPlane / slip（推荐）",
                            "freestream 自由流边界",
                            "open 开放边界（允许流入流出）",
                            "自定义",
                        ],
                        blocks=True,
                    ))

        # 下边界如果未明确
        if "下壁面" in user_text or "底" in user_text:
            bottom_bc = spec.boundaries.bottom_flat.semantic_type if hasattr(spec.boundaries, 'bottom_flat') else None
            # 如果底部有障碍物但边界条件未设
            if spec.has_triangle and "无滑移" not in user_text and "no-slip" not in user_text.lower():
                result.issues.append(AuditIssue(
                    category=IssueCategory.SOLVER_CRITICAL_AMBIGUITY,
                    code="BOTTOM_BOUNDARY_AMBIGUITY",
                    title="下壁面边界条件未明确",
                    description="下壁面有障碍物但未明确指定无滑移条件。",
                    recommendation="no_slip_wall（推荐）",
                    options=[
                        "no_slip_wall 无滑移（推荐）",
                        "slip_wall 滑移",
                        "自定义",
                    ],
                    blocks=True,
                ))

    def _check_reynolds_characteristic_length(
        self,
        spec: CylinderFlow2DExperimentSpecV1,
        user_text: str,
        result: AuditResult,
    ) -> None:
        """检测 Reynolds 特征长度歧义。"""
        has_re = bool(re.search(r'Re\s*[:=]\s*\d+', user_text, re.IGNORECASE))
        has_radius = bool(re.search(r'半径|radius|R\s*=', user_text, re.IGNORECASE))
        has_diameter = bool(re.search(r'直径|diameter|D\s*=', user_text, re.IGNORECASE))

        if has_re and has_radius and not has_diameter:
            # 用户给了 Re 和半径，但没有明确说 Re 基于直径还是半径
            # 标准做法是 Re 基于 D，但有些教科书基于 R
            result.issues.append(AuditIssue(
                category=IssueCategory.NON_BLOCKING_ASSUMPTION,
                code="REYNOLDS_CHARACTERISTIC_LENGTH",
                title="Reynolds 数特征长度假设",
                description=(
                    "用户提供了 Re 和半径 R，但未明确 Re 的特征长度。"
                    "标准做法：Re 基于 D=2R（圆柱直径）。"
                ),
                recommendation="Re 基于 D=2R（圆柱直径），这是 CFD 标准做法",
                blocks=False,
            ))

    # ------------------------------------------------------------------
    # NON_BLOCKING_ASSUMPTION
    # ------------------------------------------------------------------

    def _check_triangle_assumptions(
        self,
        spec: CylinderFlow2DExperimentSpecV1,
        user_text: str,
        result: AuditResult,
    ) -> None:
        """检测三角形的非阻断默认假设。"""
        if not spec.has_triangle:
            return

        text_lower = user_text.lower()
        assumptions = []

        # 0. 尺寸推导假设（用户未给出具体尺寸时）
        if spec.triangle.base_width_m.source == FieldSource.FORMULA_DERIVED:
            assumptions.append(
                f"底宽={spec.triangle.base_width_m.value}m（根据圆柱直径推导：2D）"
            )
        if spec.triangle.height_m.source == FieldSource.FORMULA_DERIVED:
            assumptions.append(
                f"高度={spec.triangle.height_m.value}m（根据圆柱直径推导：D）"
            )
        if spec.triangle.center_x_m.source == FieldSource.FORMULA_DERIVED:
            assumptions.append(
                f"位置x={spec.triangle.center_x_m.value}m（对齐圆柱圆心）"
            )

        # 1. 等腰三角形假设
        if "等腰" not in user_text and "等边" not in user_text and "isosceles" not in text_lower:
            assumptions.append("默认为等腰三角形")

        # 2. 底边贴附下壁面
        if "贴附" in user_text or "贴墙" in user_text or "下壁面" in user_text:
            pass  # 用户已明确
        elif spec.triangle.attached_boundary == "bottom_wall":
            assumptions.append("底边贴附下壁面")

        # 3. 尖端方向
        if "尖端" not in user_text and "顶点" not in user_text and "apex" not in text_lower:
            assumptions.append(f"尖端朝{spec.triangle.apex_direction}")

        # 4. 中心线对齐
        if "正下方" in user_text or "对齐" in user_text:
            pass  # 用户已明确
        elif spec.triangle.relation_to_cylinder == "aligned_below":
            assumptions.append("中心线与圆柱圆心 x 坐标一致")

        if assumptions:
            result.issues.append(AuditIssue(
                category=IssueCategory.NON_BLOCKING_ASSUMPTION,
                code="TRIANGLE_DEFAULT_ASSUMPTIONS",
                title="三角形障碍物默认假设",
                description="已识别为三角形障碍物（非 cosine bell）。以下为默认假设：\n" + "\n".join(f"  - {a}" for a in assumptions),
                recommendation="使用推荐几何",
                options=[
                    "使用推荐几何",
                    "修改三角形方向或形状",
                ],
                blocks=False,
            ))

    def _check_cylinder_position_assumptions(
        self,
        spec: CylinderFlow2DExperimentSpecV1,
        user_text: str,
        result: AuditResult,
    ) -> None:
        """检测圆柱位置的非阻断假设。"""
        # 如果圆柱 X 未指定但用户说"居中"
        if "居中" in user_text or "正中央" in user_text or "中央" in user_text:
            cx = spec.cylinder.center_x_m.value
            domain_l = spec.domain.length_m.value
            if cx is None and domain_l is not None and domain_l > 0:
                result.issues.append(AuditIssue(
                    category=IssueCategory.NON_BLOCKING_ASSUMPTION,
                    code="CYLINDER_X_CENTER_ASSUMPTION",
                    title="圆柱水平位置假设",
                    description=f"用户提到'居中'，将圆柱 x 坐标设为域中心 x={domain_l / 2.0} m。",
                    recommendation=f"x = {domain_l / 2.0} m",
                    blocks=False,
                ))

    # ------------------------------------------------------------------
    # DERIVED_VALUE
    # ------------------------------------------------------------------

    def _collect_derived_values(
        self,
        derivation_result: Any,
        result: AuditResult,
    ) -> None:
        """收集所有推导值，作为 DERIVED_VALUE 展示。"""
        for d in derivation_result.derivations:
            result.issues.append(AuditIssue(
                category=IssueCategory.DERIVED_VALUE,
                code=f"DERIVED_{d.target_field.upper()}",
                title=f"已推导: {d.target_field}",
                description=d.to_display(),
                derivations=[d.to_dict()],
                recommendation=f"使用推导值: {d.value} {d.unit}",
                blocks=False,
            ))

    # ------------------------------------------------------------------
    # TRUE_MISSING_FIELD
    # ------------------------------------------------------------------

    def _check_truly_missing(
        self,
        spec: CylinderFlow2DExperimentSpecV1,
        user_text: str,
        result: AuditResult,
        derivation_result: Any,
    ) -> None:
        """检测真正缺失的字段（排除可推导的）。"""
        derived_fields = set()
        if derivation_result is not None:
            derived_fields = derivation_result.derived_field_names
            derived_fields.update(derivation_result.blocked_missing_fields)

        # 圆柱半径/直径都缺失
        # 检查用户是否提到了圆柱（即使尺寸未给）
        cylinder_mentioned = spec.cylinder.type == "cylinder"
        if cylinder_mentioned:
            r = spec.cylinder.radius_m.value
            d = spec.cylinder.diameter_m.value
            if r is None and d is None and "cylinder_diameter" not in derived_fields:
                result.issues.append(AuditIssue(
                    category=IssueCategory.TRUE_MISSING_FIELD,
                    code="CYLINDER_DIMENSION_TRULY_MISSING",
                    title="圆柱尺寸缺失",
                    description="用户提到了圆柱绕流，但圆柱半径或直径未指定，且无法从其他参数推导。",
                    blocks=True,
                ))

        # 入口速度缺失
        u = spec.boundaries.left.inlet_velocity
        if u is None and "inlet_velocity" not in derived_fields:
            result.issues.append(AuditIssue(
                category=IssueCategory.TRUE_MISSING_FIELD,
                code="INLET_VELOCITY_TRULY_MISSING",
                title="入口速度缺失",
                description="来流速度未指定，且无法从其他参数推导。",
                blocks=True,
            ))

        # 域尺寸缺失
        if spec.domain.length_m.value is None:
            result.issues.append(AuditIssue(
                category=IssueCategory.TRUE_MISSING_FIELD,
                code="DOMAIN_LENGTH_TRULY_MISSING",
                title="计算域长度缺失",
                description="计算域长度未指定。",
                blocks=True,
            ))
