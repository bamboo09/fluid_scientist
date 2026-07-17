"""CylinderFlow2DV1Pipeline — multi-pass reasoning pipeline.

Implements Section 3.3 of the plan.  Orchestrates 6 passes:

  Pass 1: Fact extraction (from user text)
  Pass 2: Ambiguity and conflict identification
  Pass 3: Scientific semantic normalization
  Pass 4: Deterministic field derivation (CODE, not LLM)
  Pass 5: Observable extraction and recommendation
  Pass 6: Critic (independent review + auto-repair)

Key principles:
- Fast model for classification/simple tasks
- Reasoning model for scientific understanding
- No chain-of-thought saved — only structured DecisionSummary
- Model recommendations NEVER override user-explicit values
"""

from __future__ import annotations

import re
import math
from dataclasses import dataclass, field
from typing import Any

from fluid_scientist.cylinder_flow_2d.models import (
    AnalysisGoalSpec,
    BoundarySpec,
    BumpProfileType,
    CylinderFlow2DExperimentSpecV1,
    CylinderSpec,
    CylinderWallType,
    DecisionSummary,
    DomainSpec,
    DraftStatus,
    FieldSource,
    FieldStatus,
    FlowMode,
    FlowRegime,
    FluidSpec,
    ForcingSpec,
    InletProfileSpec,
    InitialConditionsSpec,
    ModelPolicy,
    ObservableSpec,
    ObservableType,
    ProvenanceField,
    SemanticBoundaryType,
    SimulationSpec,
    TemporalType,
    TimeMode,
    TriangleSpec,
)


@dataclass
class PipelineStageResult:
    """Result of a single pipeline stage."""

    stage_name: str
    success: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class PipelineRunResult:
    """Complete result of running the pipeline."""

    spec: CylinderFlow2DExperimentSpecV1
    stage_history: list[PipelineStageResult] = field(default_factory=list)
    decision_summary: DecisionSummary = field(default_factory=DecisionSummary)
    pipeline_id: str = "cylinder-flow-2d-v1"
    schema_name: str = "CylinderFlow2DExperimentSpecV1"
    pipeline_version: str = "1.0"
    pipeline_stage: str = "DRAFT_NORMALIZED"


class CylinderFlow2DV1Pipeline:
    """Multi-pass pipeline for CylinderFlow2D draft generation.

    Usage:
        pipeline = CylinderFlow2DV1Pipeline()
        result = pipeline.run("二维圆柱绕流，半径R=0.1m，距下壁面2m...")
        spec = result.spec
        status = spec.draft_status
    """

    PIPELINE_ID = "cylinder-flow-2d-v1"
    SCHEMA_NAME = "CylinderFlow2DExperimentSpecV1"
    PIPELINE_VERSION = "1.0"

    def __init__(
        self,
        model_policy: ModelPolicy | None = None,
        llm_client: Any = None,
    ) -> None:
        self.model_policy = model_policy or ModelPolicy()
        self.llm_client = llm_client

    def run(self, user_text: str) -> PipelineRunResult:
        """Run the full 6-pass pipeline on user text.

        Args:
            user_text: The user's natural language input.

        Returns:
            PipelineRunResult with the complete spec and stage history.
        """
        # Preprocess: strip LaTeX math delimiters $...$ and \(...\) and \[...\]
        # so that "$0.1$ m" becomes "0.1 m" for regex extraction
        user_text = re.sub(r'\$+', '', user_text)
        user_text = re.sub(r'\\\(', '', user_text)
        user_text = re.sub(r'\\\)', '', user_text)
        user_text = re.sub(r'\\\[', '', user_text)
        user_text = re.sub(r'\\\]', '', user_text)

        spec = CylinderFlow2DExperimentSpecV1(
            user_input_text=user_text,
            pipeline_id=self.PIPELINE_ID,
            schema_version="1.0",
        )

        result = PipelineRunResult(spec=spec)
        decision = DecisionSummary()

        # Pass 1: Fact extraction
        stage = self._pass1_fact_extraction(spec, user_text, decision)
        result.stage_history.append(stage)

        # Pass 2: Ambiguity detection
        stage = self._pass2_ambiguity_detection(spec, user_text, decision)
        result.stage_history.append(stage)

        # Pass 3: Scientific normalization
        stage = self._pass3_scientific_normalization(spec, user_text, decision)
        result.stage_history.append(stage)

        # Pass 4: Deterministic field derivation (CODE, not LLM)
        stage = self._pass4_deterministic_derivation(spec, decision)
        result.stage_history.append(stage)

        # Pass 4b: Re-run ambiguity audit with derivation results
        # Now that physics dependencies are resolved, the auditor can
        # classify DERIVED_VALUE issues and filter derivable missing fields
        from fluid_scientist.cylinder_flow_2d.physics_dependency import (
            PhysicsDependencyResolver,
        )
        physics_resolver_2 = PhysicsDependencyResolver()
        derivation_result_2 = physics_resolver_2.resolve(spec)

        from fluid_scientist.cylinder_flow_2d.ambiguity_audit import (
            AmbiguityAndConflictAuditor,
        )
        auditor_2 = AmbiguityAndConflictAuditor()
        audit_result_2 = auditor_2.audit(spec, user_text, derivation_result=derivation_result_2)

        # Update ambiguities with post-derivation audit (includes DERIVED_VALUE)
        for issue in audit_result_2.issues:
            if issue.category.value == "DERIVED_VALUE":
                decision.derived_values.append(issue.description)
            elif issue.category.value == "NON_BLOCKING_ASSUMPTION":
                if issue.description not in " ".join(decision.assumptions):
                    decision.assumptions.append(f"[假设] {issue.title}: {issue.description}")

        # Pass 5: Observable extraction and recommendation
        stage = self._pass5_observables(spec, user_text, decision)
        result.stage_history.append(stage)

        # Pass 5b: Analysis goals
        stage = self._pass5b_analysis_goals(spec, decision)
        result.stage_history.append(stage)

        # Pass 6: Critic
        stage = self._pass6_critic(spec, user_text, decision)
        result.stage_history.append(stage)

        # Final: Readiness evaluation
        from fluid_scientist.cylinder_flow_2d.readiness import (
            CylinderFlow2DDraftReadinessEvaluator,
        )
        evaluator = CylinderFlow2DDraftReadinessEvaluator()
        final_status = evaluator.evaluate(spec)

        # Update decision summary
        decision.confidence = self._compute_confidence(spec)
        spec.decision_summary = decision
        result.decision_summary = decision

        return result

    # -----------------------------------------------------------------------
    # Pass 1: Fact Extraction
    # -----------------------------------------------------------------------

    def _pass1_fact_extraction(
        self,
        spec: CylinderFlow2DExperimentSpecV1,
        user_text: str,
        decision: DecisionSummary,
    ) -> PipelineStageResult:
        """Extract only what the user explicitly stated."""
        stage = PipelineStageResult(stage_name="fact_extraction")
        text_lower = user_text.lower()

        facts: list[str] = []

        # Detect 2D
        if "二维" in user_text or "2d" in text_lower or "2 d" in text_lower:
            facts.append("用户指定了二维流场")
            spec.domain.dimensionality = "2D"

        # Detect cylinder
        cylinder_words = ["圆柱", "圆形障碍物", "圆形物体", "cylinder", "circular body", "circular obstacle"]
        if any(w in text_lower for w in [w.lower() for w in cylinder_words]):
            facts.append("用户描述了圆柱")
            spec.cylinder.type = "cylinder"

        # Extract radius: R=0.1, R = 0.1m, 半径0.1, 半径=0.1, radius=0.1
        radius = self._extract_radius(user_text)
        if radius is not None:
            facts.append(f"用户指定了圆柱半径 R={radius} m")
            spec.cylinder.radius_m = ProvenanceField(
                value=radius,
                source=FieldSource.USER_EXPLICIT,
                status=FieldStatus.RESOLVED,
                confidence=1.0,
                reason="用户明确指定",
            )

        # Extract diameter
        diameter = self._extract_diameter(user_text)
        if diameter is not None:
            facts.append(f"用户指定了圆柱直径 D={diameter} m")
            spec.cylinder.diameter_m = ProvenanceField(
                value=diameter,
                source=FieldSource.USER_EXPLICIT,
                status=FieldStatus.RESOLVED,
                confidence=1.0,
                reason="用户明确指定",
            )

        # Extract domain dimensions
        domain = self._extract_domain(user_text)
        if domain:
            if "length" in domain:
                facts.append(f"用户指定了计算域长度 {domain['length']} m")
                spec.domain.length_m = ProvenanceField(
                    value=domain["length"],
                    source=FieldSource.USER_EXPLICIT,
                    status=FieldStatus.RESOLVED,
                    confidence=1.0,
                )
            if "height" in domain:
                facts.append(f"用户指定了计算域高度 {domain['height']} m")
                spec.domain.height_m = ProvenanceField(
                    value=domain["height"],
                    source=FieldSource.USER_EXPLICIT,
                    status=FieldStatus.RESOLVED,
                    confidence=1.0,
                )

        # Extract cylinder position
        position = self._extract_cylinder_position(user_text)
        if position:
            if "x" in position:
                facts.append(f"用户指定了圆柱x位置 {position['x']} m")
                spec.cylinder.center_x_m = ProvenanceField(
                    value=position["x"],
                    source=FieldSource.USER_EXPLICIT,
                    status=FieldStatus.RESOLVED,
                    confidence=1.0,
                )
            if "y" in position:
                facts.append(f"用户指定了圆柱y位置 {position['y']} m")
                spec.cylinder.center_y_m = ProvenanceField(
                    value=position["y"],
                    source=FieldSource.USER_EXPLICIT,
                    status=FieldStatus.RESOLVED,
                    confidence=1.0,
                )

        # Extract wall distance
        wall_dist = self._extract_wall_distance(user_text)
        if wall_dist is not None:
            # If user explicitly says "圆心距" (center distance), set center_y directly
            if "圆心距" in user_text or "圆心高度" in user_text or "中心距" in user_text:
                facts.append(f"用户指定了圆心距下壁面 {wall_dist} m（圆心高度）")
                spec.cylinder.center_y_m = ProvenanceField(
                    value=wall_dist,
                    source=FieldSource.USER_EXPLICIT,
                    status=FieldStatus.RESOLVED,
                    confidence=1.0,
                    reason="用户明确指定圆心距下壁面",
                )
            else:
                facts.append(f"用户提到了圆柱距壁面 {wall_dist} m（语义待确认）")

        # Extract "正中央" / "居中" / "中央" — user explicitly says cylinder is centered
        # This sets center_x = domain_length/2 as USER_EXPLICIT
        # If center_y is not yet resolved AND no wall distance was given, also set center_y = domain_height/2
        center_phrases = ["正中央", "流场中央", "流场中心", "位于中央", "位于中心",
                          "水平居中", "水平中心", "居中放置", "正中"]
        user_says_centered = any(p in user_text for p in center_phrases)
        if user_says_centered:
            domain_l = spec.domain.length_m.value
            domain_h = spec.domain.height_m.value
            # Set center_x = domain_length / 2 (horizontal centering)
            if domain_l is not None and domain_l > 0 and not spec.cylinder.center_x_m.is_resolved():
                cx = domain_l / 2.0
                facts.append(f"用户指定'位于流场正中央'，圆心x = 域长/2 = {cx} m")
                spec.cylinder.center_x_m = ProvenanceField(
                    value=cx,
                    source=FieldSource.USER_EXPLICIT,
                    status=FieldStatus.RESOLVED,
                    confidence=0.95,
                    reason="用户明确指定'位于流场正中央'，水平居中",
                )
            # Only set center_y from "正中央" if no wall distance was given
            if (domain_h is not None and domain_h > 0
                    and not spec.cylinder.center_y_m.is_resolved()
                    and wall_dist is None):
                cy = domain_h / 2.0
                facts.append(f"用户指定'位于流场正中央'，圆心y = 域高/2 = {cy} m")
                spec.cylinder.center_y_m = ProvenanceField(
                    value=cy,
                    source=FieldSource.USER_EXPLICIT,
                    status=FieldStatus.RESOLVED,
                    confidence=0.8,
                    reason="用户指定'位于流场正中央'，垂直居中（无距壁面信息）",
                )

        # Extract inlet velocity
        inlet_v = self._extract_inlet_velocity(user_text)
        if inlet_v is not None:
            facts.append(f"用户指定了来流速度 {inlet_v} m/s")
            spec.boundaries.left.inlet_velocity = inlet_v

        # Detect boundary descriptions
        if "无滑移" in user_text or "no-slip" in text_lower or "noslip" in text_lower:
            if "下" in user_text or "底" in user_text or "bottom" in text_lower:
                facts.append("用户指定了底部无滑移")
                spec.boundaries.bottom_flat = BoundarySpec(
                    semantic_type=SemanticBoundaryType.NO_SLIP_WALL,
                    source=FieldSource.USER_EXPLICIT,
                    status=FieldStatus.RESOLVED,
                    confidence=1.0,
                )

        # Detect top no-slip wall: "上无滑移", "顶无滑移", "top no-slip"
        if "无滑移" in user_text or "no-slip" in text_lower or "noslip" in text_lower:
            if "顶" in user_text or ("上" in user_text and ("无滑移" in user_text or "no-slip" in text_lower)):
                # Verify "无滑移" appears near "上"/"顶"
                for m in re.finditer(r"无滑移|no.?slip|noslip", text_lower):
                    start = m.start()
                    window = user_text[max(0, start - 10):start + 10]
                    if "上" in window or "顶" in window or "top" in window.lower():
                        facts.append("用户指定了顶部无滑移")
                        spec.boundaries.top = BoundarySpec(
                            semantic_type=SemanticBoundaryType.NO_SLIP_WALL,
                            source=FieldSource.USER_EXPLICIT,
                            status=FieldStatus.RESOLVED,
                            confidence=1.0,
                        )
                        break

        # Detect top slip wall — exclude "无滑移" false positive
        # Only trigger if there's a standalone "滑移" (not part of "无滑移") near "上"/"顶"
        if "滑移" in user_text and ("顶" in user_text or "上" in user_text or "top" in text_lower):
            # Check: is there a "滑移" that is NOT preceded by "无"?
            has_standalone_slip = False
            for m in re.finditer(r"滑移", user_text):
                start = m.start()
                if start == 0 or user_text[start - 1] != "无":
                    has_standalone_slip = True
                    break
            if has_standalone_slip:
                facts.append("用户指定了顶部滑移")
                spec.boundaries.top = BoundarySpec(
                    semantic_type=SemanticBoundaryType.SLIP_WALL,
                    source=FieldSource.USER_EXPLICIT,
                    status=FieldStatus.RESOLVED,
                    confidence=1.0,
                )

        # Detect bottom slip wall: "底部滑移", "底...滑移"
        if "滑移" in user_text and ("底" in user_text or "下" in user_text or "bottom" in text_lower):
            if "无滑移" not in user_text and "no-slip" not in text_lower and "noslip" not in text_lower:
                facts.append("用户指定了底部滑移")
                spec.boundaries.bottom_flat = BoundarySpec(
                    semantic_type=SemanticBoundaryType.SLIP_WALL,
                    source=FieldSource.USER_EXPLICIT,
                    status=FieldStatus.RESOLVED,
                    confidence=1.0,
                )

        if "自由流" in user_text or "freestream" in text_lower or "free stream" in text_lower:
            if "顶" in user_text or "上" in user_text:
                facts.append("用户指定了顶部自由流")
                spec.boundaries.top = BoundarySpec(
                    semantic_type=SemanticBoundaryType.FREESTREAM,
                    source=FieldSource.USER_EXPLICIT,
                    status=FieldStatus.RESOLVED,
                    confidence=1.0,
                )

        # 自由出流 (free outflow) — open boundary that lets flow exit freely
        if "自由出流" in user_text or "自由出口" in user_text:
            # Use proximity check: "自由出流" must be near "顶"/"上" to set top
            for m in re.finditer(r"自由出流|自由出口", user_text):
                start = m.start()
                window = user_text[max(0, start - 5):start + 5]
                if "顶" in window or "上" in window:
                    facts.append("用户指定了顶部自由出流")
                    spec.boundaries.top = BoundarySpec(
                        semantic_type=SemanticBoundaryType.OPEN_BOUNDARY,
                        source=FieldSource.USER_EXPLICIT,
                        status=FieldStatus.RESOLVED,
                        confidence=1.0,
                    )
                    break

        if "周期" in user_text or "periodic" in text_lower:
            facts.append("用户提到了周期边界")
            spec.boundaries.left = BoundarySpec(
                semantic_type=SemanticBoundaryType.PERIODIC,
                source=FieldSource.USER_EXPLICIT,
                status=FieldStatus.RESOLVED,
                confidence=1.0,
            )
            spec.boundaries.right = BoundarySpec(
                semantic_type=SemanticBoundaryType.PERIODIC,
                source=FieldSource.USER_EXPLICIT,
                status=FieldStatus.RESOLVED,
                confidence=1.0,
            )

        if "压力出口" in user_text or "pressure outlet" in text_lower:
            facts.append("用户指定了压力出口")
            spec.boundaries.right = BoundarySpec(
                semantic_type=SemanticBoundaryType.PRESSURE_OUTLET,
                source=FieldSource.USER_EXPLICIT,
                status=FieldStatus.RESOLVED,
                confidence=1.0,
                pressure_value=0.0,
            )

        # Detect symmetry boundary: "对称", "symmetry", "symmetryPlane"
        if "对称" in user_text or "symmetry" in text_lower:
            for m in re.finditer(r"对称|symmetry", text_lower):
                start = m.start()
                window = user_text[max(0, start - 5):start + 10]
                if "顶" in window or "上" in window or "top" in window.lower():
                    facts.append("用户指定了顶部对称边界")
                    spec.boundaries.top = BoundarySpec(
                        semantic_type=SemanticBoundaryType.SYMMETRY,
                        source=FieldSource.USER_EXPLICIT,
                        status=FieldStatus.RESOLVED,
                        confidence=1.0,
                    )
                    break
                elif "底" in window or "下" in window or "bottom" in window.lower():
                    facts.append("用户指定了底部对称边界")
                    spec.boundaries.bottom_flat = BoundarySpec(
                        semantic_type=SemanticBoundaryType.SYMMETRY,
                        source=FieldSource.USER_EXPLICIT,
                        status=FieldStatus.RESOLVED,
                        confidence=1.0,
                    )
                    break

        if "来流" in user_text or "入口" in user_text or "inlet" in text_lower or "流入" in user_text or "恒速" in user_text or "速度入口" in user_text:
            if spec.boundaries.left.semantic_type is None:
                facts.append("用户提到了来流/入口/流入")
                spec.boundaries.left = BoundarySpec(
                    semantic_type=SemanticBoundaryType.UNIFORM_VELOCITY_INLET,
                    source=FieldSource.USER_EXPLICIT,
                    status=FieldStatus.RESOLVED if inlet_v is not None else FieldStatus.PARTIALLY_RESOLVED,
                    confidence=1.0,
                    inlet_velocity=inlet_v,
                )

        # Extract fluid
        if "水" in user_text or "water" in text_lower:
            facts.append("用户指定了流体为水")
            spec.fluid.type = ProvenanceField(
                value="water",
                source=FieldSource.USER_EXPLICIT,
                status=FieldStatus.RESOLVED,
                confidence=1.0,
            )

        if "空气" in user_text or "air" in text_lower:
            facts.append("用户指定了流体为空气")
            spec.fluid.type = ProvenanceField(
                value="air",
                source=FieldSource.USER_EXPLICIT,
                status=FieldStatus.RESOLVED,
                confidence=1.0,
            )

        # Extract fluid density
        density = self._extract_density(user_text)
        if density is not None:
            facts.append(f"用户指定了流体密度 {density} kg/m3")
            spec.fluid.density_kg_m3 = ProvenanceField(
                value=density,
                source=FieldSource.USER_EXPLICIT,
                status=FieldStatus.RESOLVED,
                confidence=1.0,
            )

        # Extract kinematic viscosity
        viscosity = self._extract_viscosity(user_text)
        if viscosity is not None:
            facts.append(f"用户指定了运动粘度 {viscosity} m2/s")
            spec.fluid.kinematic_viscosity_m2_s = ProvenanceField(
                value=viscosity,
                source=FieldSource.USER_EXPLICIT,
                status=FieldStatus.RESOLVED,
                confidence=1.0,
            )

        # Extract angular velocity (rotating cylinder)
        angular_vel = self._extract_angular_velocity(user_text)
        if angular_vel is not None:
            facts.append(f"用户指定了圆柱角速度 {angular_vel} rad/s")
            spec.cylinder.angular_velocity_rad_s = angular_vel
            spec.cylinder.wall_type = CylinderWallType.ROTATING_WALL
            # Detect rotation direction
            if "顺时针" in user_text or "clockwise" in text_lower or "cw" in text_lower.split():
                spec.cylinder.rotation_direction = "cw"
            else:
                spec.cylinder.rotation_direction = "ccw"

        # Extract bottom profile (bump) — only for actual bump shapes
        # NOT for triangle, rectangle, or other polygon obstacles
        bump = self._extract_bump(user_text)
        if bump:
            facts.append(
                f"用户指定了底面凸起: 类型={bump.get('profile_type', 'unspecified')}, "
                f"高={bump.get('height')}, 宽={bump.get('width')}"
            )
            spec.bottom_profile.enabled = True
            # Only set profile_type if user explicitly named a bump shape
            if "cosine" in text_lower or "余弦" in user_text:
                spec.bottom_profile.profile_type = BumpProfileType.COSINE_BELL
            elif "sine" in text_lower or "正弦" in user_text:
                spec.bottom_profile.profile_type = BumpProfileType.HALF_SINE
            elif "gaussian" in text_lower or "高斯" in user_text:
                spec.bottom_profile.profile_type = BumpProfileType.GAUSSIAN
            else:
                # No explicit bump shape — do NOT default to cosine_bell
                spec.bottom_profile.profile_type = BumpProfileType.FLAT

            if bump.get("height") is not None:
                spec.bottom_profile.height_m = ProvenanceField(
                    value=bump["height"],
                    source=FieldSource.USER_EXPLICIT,
                    status=FieldStatus.RESOLVED,
                    confidence=1.0,
                )
            if bump.get("width") is not None:
                spec.bottom_profile.width_m = ProvenanceField(
                    value=bump["width"],
                    source=FieldSource.USER_EXPLICIT,
                    status=FieldStatus.RESOLVED,
                    confidence=1.0,
                )
            if bump.get("center_x") is not None:
                spec.bottom_profile.center_x_m = ProvenanceField(
                    value=bump["center_x"],
                    source=FieldSource.USER_EXPLICIT,
                    status=FieldStatus.RESOLVED,
                    confidence=1.0,
                )
            # Store alignment flag for physics_dependency to use
            if bump.get("aligned_below_cylinder"):
                spec.bottom_profile.aligned_below_cylinder = True

        # Extract triangle obstacle — semantic_type = triangle_2d
        # NEVER substitute with cosine_bell or any other shape
        triangle = self._extract_triangle(user_text)
        if triangle:
            facts.append(
                f"用户指定了三角形障碍物: "
                f"高={triangle.get('height')}, 底宽={triangle.get('base_width')}, "
                f"方向={triangle.get('apex_direction', 'up')}"
            )
            spec.triangle.enabled = True
            spec.triangle.semantic_type = "triangle_2d"
            spec.triangle.solver_representation = "polygon"
            spec.triangle.source_text = user_text
            if triangle.get("height") is not None:
                spec.triangle.height_m = ProvenanceField(
                    value=triangle["height"],
                    source=FieldSource.USER_EXPLICIT,
                    status=FieldStatus.RESOLVED,
                    confidence=1.0,
                    reason="用户明确指定三角形高度",
                )
            if triangle.get("base_width") is not None:
                spec.triangle.base_width_m = ProvenanceField(
                    value=triangle["base_width"],
                    source=FieldSource.USER_EXPLICIT,
                    status=FieldStatus.RESOLVED,
                    confidence=1.0,
                    reason="用户明确指定三角形底宽",
                )
            if triangle.get("center_x") is not None:
                spec.triangle.center_x_m = ProvenanceField(
                    value=triangle["center_x"],
                    source=FieldSource.USER_EXPLICIT,
                    status=FieldStatus.RESOLVED,
                    confidence=1.0,
                    reason="用户明确指定三角形位置",
                )
            if triangle.get("apex_direction"):
                spec.triangle.apex_direction = triangle["apex_direction"]
            if triangle.get("attached_boundary"):
                spec.triangle.attached_boundary = triangle["attached_boundary"]
            if triangle.get("aligned_below_cylinder"):
                spec.triangle.relation_to_cylinder = "aligned_below"

        # Extract rectangle obstacle — semantic_type = rectangle_2d
        rectangle = self._extract_rectangle(user_text, spec)
        if rectangle:
            facts.append(
                f"用户指定了矩形障碍物: "
                f"高={rectangle.get('height')}, 宽={rectangle.get('width')}, "
                f"位置x={rectangle.get('center_x')}"
            )
            spec.rectangle.enabled = True
            if rectangle.get("height") is not None:
                spec.rectangle.height_m = ProvenanceField(
                    value=rectangle["height"],
                    source=FieldSource.USER_EXPLICIT,
                    status=FieldStatus.RESOLVED,
                    confidence=1.0,
                    reason="用户明确指定矩形高度",
                )
            if rectangle.get("width") is not None:
                spec.rectangle.width_m = ProvenanceField(
                    value=rectangle["width"],
                    source=FieldSource.USER_EXPLICIT,
                    status=FieldStatus.RESOLVED,
                    confidence=1.0,
                    reason="用户明确指定矩形宽度",
                )
            if rectangle.get("center_x") is not None:
                spec.rectangle.center_x_m = ProvenanceField(
                    value=rectangle["center_x"],
                    source=FieldSource.USER_EXPLICIT,
                    status=FieldStatus.RESOLVED,
                    confidence=1.0,
                    reason="用户明确指定矩形位置",
                )
            if rectangle.get("aligned_below_cylinder"):
                spec.rectangle.relation_to_cylinder = "aligned_below"

        # Extract end time
        end_time = self._extract_end_time(user_text)
        if end_time is not None:
            facts.append(f"用户指定了仿真时间 {end_time} 秒")
            spec.simulation.end_time = end_time

        # Extract Reynolds number and set flow regime
        reynolds = self._extract_reynolds(user_text)
        if reynolds is not None:
            facts.append(f"用户指定了雷诺数 Re={reynolds}")
            if reynolds < 2000:
                spec.simulation.flow_regime = FlowRegime.LAMINAR
                facts.append(f"Re={reynolds} < 2000，流态判定为层流（laminar）")
            else:
                spec.simulation.flow_regime = FlowRegime.TURBULENT
                facts.append(f"Re={reynolds} >= 2000，流态判定为湍流（turbulent）")

        # Extract observation targets
        if "截面" in user_text and ("平均" in user_text or "流速" in user_text or "速度" in user_text):
            facts.append("用户要求观测截面平均流速")
        if "点" in user_text and ("平均" in user_text or "流速" in user_text or "速度" in user_text):
            facts.append("用户要求观测点平均流速")
        if "阻力" in user_text or "drag" in text_lower:
            facts.append("用户要求观测圆柱阻力")
        if "升力" in user_text or "lift" in text_lower:
            facts.append("用户要求观测圆柱升力")

        decision.facts = facts
        return stage

    # -----------------------------------------------------------------------
    # Pass 2: Ambiguity Detection
    # -----------------------------------------------------------------------

    def _pass2_ambiguity_detection(
        self,
        spec: CylinderFlow2DExperimentSpecV1,
        user_text: str,
        decision: DecisionSummary,
    ) -> PipelineStageResult:
        """Identify ambiguities and conflicts using the full auditor."""
        stage = PipelineStageResult(stage_name="ambiguity_detection")
        ambiguities: list[dict] = []

        # Use the comprehensive AmbiguityAndConflictAuditor
        from fluid_scientist.cylinder_flow_2d.ambiguity_audit import (
            AmbiguityAndConflictAuditor,
        )
        auditor = AmbiguityAndConflictAuditor()
        # Note: derivation_result is None here because Pass 4 hasn't run yet
        # The auditor will still detect conflicts and ambiguities
        audit_result = auditor.audit(spec, user_text, derivation_result=None)

        for issue in audit_result.issues:
            amb_entry = {
                "id": issue.code,
                "category": issue.category.value,
                "description": issue.description,
                "options": issue.options,
                "recommendation": issue.recommendation,
                "resolved": not issue.blocks,
            }
            ambiguities.append(amb_entry)

            if issue.blocks:
                decision.unresolved_items.append(
                    f"[{issue.category.value}] {issue.title}: {issue.description}"
                )
            elif issue.category.value == "NON_BLOCKING_ASSUMPTION":
                decision.assumptions.append(
                    f"[假设] {issue.title}: {issue.description}"
                )

        spec.ambiguities = ambiguities

        # "距壁面2 m" — is it center height or surface gap?
        wall_dist = self._extract_wall_distance(user_text)
        if wall_dist is not None and spec.cylinder.center_y_m.value is None:
            ambiguities.append({
                "id": "cylinder_wall_distance_meaning",
                "description": f"圆柱距下壁面 {wall_dist} m 的含义不明确：是圆心高度还是表面间隙？",
                "options": [
                    f"圆心高度为 {wall_dist} 米",
                    f"圆柱表面与壁面间隙为 {wall_dist} 米",
                ],
                "resolved": False,
            })

        # "自由出口" or "自由出流" — which type of open boundary?
        if ("自由出口" in user_text or "自由出流" in user_text) and "压力出口" not in user_text and "压力入口" not in user_text:
            # Only ambiguous if it's not already clear from context
            if "自由出流" not in user_text:  # 自由出流 is unambiguous — it's an open boundary
                ambiguities.append({
                    "id": "open_outlet_type",
                    "description": "自由出口的具体类型未明确：是压力出口、开放出口还是对流出口？",
                    "options": ["压力出口", "开放出口", "对流出口"],
                    "resolved": False,
                })

        # "向右应力" — shear stress or moving wall?
        if "应力" in user_text and "向右" in user_text:
            ambiguities.append({
                "id": "shear_vs_moving",
                "description": "向右应力是剪切应力还是运动壁面？",
                "options": ["剪切应力", "运动壁面"],
                "resolved": False,
            })

        # Pressure gradient missing unit
        if "压力梯度" in user_text or "pressure gradient" in user_text.lower():
            if spec.forcing.pressure_gradient.magnitude.value is not None:
                if spec.forcing.pressure_gradient.unit.value is None:
                    ambiguities.append({
                        "id": "pressure_gradient_unit",
                        "description": "压力梯度单位未指定：是Pa/m还是m/s²？",
                        "options": ["Pa/m", "m/s²"],
                        "resolved": False,
                    })

        # Periodic + inlet conflict
        if spec.is_periodic and spec.boundaries.left.semantic_type == SemanticBoundaryType.UNIFORM_VELOCITY_INLET:
            ambiguities.append({
                "id": "periodic_inlet_conflict",
                "description": "周期边界与速度入口冲突",
                "resolved": False,
            })

        spec.ambiguities = ambiguities
        decision.unresolved_items = [a["id"] for a in ambiguities if not a.get("resolved")]
        return stage

    # -----------------------------------------------------------------------
    # Pass 3: Scientific Normalization
    # -----------------------------------------------------------------------

    def _pass3_scientific_normalization(
        self,
        spec: CylinderFlow2DExperimentSpecV1,
        user_text: str,
        decision: DecisionSummary,
    ) -> PipelineStageResult:
        """Normalize natural language to semantic types."""
        stage = PipelineStageResult(stage_name="scientific_normalization")
        text_lower = user_text.lower()

        # Normalize inlet
        if spec.boundaries.left.semantic_type is None:
            if "来流" in user_text or "入口" in user_text or "inlet" in text_lower:
                spec.boundaries.left = BoundarySpec(
                    semantic_type=SemanticBoundaryType.UNIFORM_VELOCITY_INLET,
                    source=FieldSource.USER_EXPLICIT,
                    status=FieldStatus.PARTIALLY_RESOLVED,
                    confidence=0.8,
                )

        # Normalize outlet
        if spec.boundaries.right.semantic_type is None:
            if "出口" in user_text or "outlet" in text_lower:
                spec.boundaries.right = BoundarySpec(
                    semantic_type=SemanticBoundaryType.PRESSURE_OUTLET,
                    source=FieldSource.MODEL_RECOMMENDED,
                    status=FieldStatus.AWAITING_CONFIRMATION,
                    confidence=0.7,
                    pressure_value=0.0,
                )

        # Normalize top boundary
        if spec.boundaries.top.semantic_type is None:
            if "滑移" in user_text and ("顶" in user_text or "上" in user_text):
                spec.boundaries.top = BoundarySpec(
                    semantic_type=SemanticBoundaryType.SLIP_WALL,
                    source=FieldSource.USER_EXPLICIT,
                    status=FieldStatus.RESOLVED,
                    confidence=1.0,
                )
            elif "自由流" in user_text or "freestream" in text_lower:
                spec.boundaries.top = BoundarySpec(
                    semantic_type=SemanticBoundaryType.FREESTREAM,
                    source=FieldSource.USER_EXPLICIT,
                    status=FieldStatus.RESOLVED,
                    confidence=1.0,
                )
            elif "自由出流" in user_text or "自由出口" in user_text:
                spec.boundaries.top = BoundarySpec(
                    semantic_type=SemanticBoundaryType.OPEN_BOUNDARY,
                    source=FieldSource.USER_EXPLICIT,
                    status=FieldStatus.RESOLVED,
                    confidence=1.0,
                )

        # Normalize bottom boundary
        if spec.boundaries.bottom_flat.semantic_type is None:
            if "无滑移" in user_text and ("下" in user_text or "底" in user_text):
                spec.boundaries.bottom_flat = BoundarySpec(
                    semantic_type=SemanticBoundaryType.NO_SLIP_WALL,
                    source=FieldSource.USER_EXPLICIT,
                    status=FieldStatus.RESOLVED,
                    confidence=1.0,
                )

        # Determine flow topology
        from fluid_scientist.cylinder_flow_2d.boundary_topology import (
            CylinderFlow2DBoundaryTopologyResolver,
        )
        resolver = CylinderFlow2DBoundaryTopologyResolver()
        try:
            flow_mode = resolver.resolve(spec)
            spec.flow_topology = {"mode": flow_mode.value}
            decision.derived_values.append(f"流动拓扑: {flow_mode.value}")
        except Exception:
            spec.flow_topology = {"mode": None}

        return stage

    # -----------------------------------------------------------------------
    # Pass 4: Deterministic Field Derivation (CODE, not LLM)
    # -----------------------------------------------------------------------

    def _pass4_deterministic_derivation(
        self,
        spec: CylinderFlow2DExperimentSpecV1,
        decision: DecisionSummary,
    ) -> PipelineStageResult:
        """Derive fields using deterministic code."""
        stage = PipelineStageResult(stage_name="deterministic_derivation")

        from fluid_scientist.cylinder_flow_2d.geometry_normalizer import (
            CylinderFlow2DGeometryNormalizer,
            CylinderFlow2DDerivedFieldResolver,
        )
        from fluid_scientist.cylinder_flow_2d.physics_dependency import (
            PhysicsDependencyResolver,
        )

        # Normalize geometry
        normalizer = CylinderFlow2DGeometryNormalizer()
        normalizer.normalize(spec, spec.user_input_text or "")

        # Derive radius ↔ diameter and characteristic_dimension
        resolver = CylinderFlow2DDerivedFieldResolver()
        resolver.resolve(spec)

        # Run full physics dependency resolution
        # This derives: nu = U*D/Re, reference_length, reference_area, etc.
        # Each derivation records formula and dependencies
        physics_resolver = PhysicsDependencyResolver()
        derivation_result = physics_resolver.resolve(spec)

        # Record all derivations in decision summary
        for d in derivation_result.derivations:
            decision.derived_values.append(d.to_display())

        # Only set water defaults if viscosity is STILL not resolved
        # (i.e., user didn't give Re, so we can't derive nu)
        if not spec.fluid.kinematic_viscosity_m2_s.is_resolved():
            decision.assumptions.append("假设流体为水（20°C）")
            spec.fluid.type = ProvenanceField(
                value="water",
                source=FieldSource.MODEL_RECOMMENDED,
                status=FieldStatus.AWAITING_CONFIRMATION,
                confidence=0.7,
                reason="默认推荐水作为流体",
            )
            spec.fluid.temperature_c = ProvenanceField(
                value=20.0,
                source=FieldSource.MODEL_RECOMMENDED,
                status=FieldStatus.AWAITING_CONFIRMATION,
                confidence=0.7,
            )
            spec.fluid.density_kg_m3 = ProvenanceField(
                value=998.0,
                source=FieldSource.MODEL_RECOMMENDED,
                status=FieldStatus.AWAITING_CONFIRMATION,
                confidence=0.7,
            )
            spec.fluid.kinematic_viscosity_m2_s = ProvenanceField(
                value=1.004e-6,
                source=FieldSource.MODEL_RECOMMENDED,
                status=FieldStatus.AWAITING_CONFIRMATION,
                confidence=0.7,
            )

        # Default domain if not specified
        if not spec.domain.length_m.is_resolved():
            # Derive from cylinder position if available
            cx = spec.cylinder.center_x_m.value
            d = spec.get_cylinder_diameter()
            if cx is not None and d is not None:
                # 10 diameters upstream + 20 diameters downstream
                recommended_length = max(cx + d * 20, cx * 2, d * 30)
                decision.assumptions.append(f"推荐计算域长度 {recommended_length} m")
                spec.domain.length_m = ProvenanceField(
                    value=recommended_length,
                    source=FieldSource.MODEL_RECOMMENDED,
                    status=FieldStatus.AWAITING_CONFIRMATION,
                    confidence=0.6,
                )
            else:
                # No cylinder position — derive from diameter
                d = spec.get_cylinder_diameter()
                if d is not None and d > 0:
                    # 10D upstream + 20D downstream = 30D total
                    recommended_length = d * 30
                else:
                    recommended_length = 30.0  # 30m default
                decision.assumptions.append(f"推荐计算域长度 {recommended_length} m")
                spec.domain.length_m = ProvenanceField(
                    value=recommended_length,
                    source=FieldSource.MODEL_RECOMMENDED,
                    status=FieldStatus.AWAITING_CONFIRMATION,
                    confidence=0.5,
                )

        if not spec.domain.height_m.is_resolved():
            d = spec.get_cylinder_diameter()
            cy = spec.cylinder.center_y_m.value
            if d is not None and cy is not None:
                # Ensure cylinder fits with at least 10D margin above
                recommended_height = max(d * 20, cy + d * 10)
                decision.assumptions.append(f"推荐计算域高度 {recommended_height} m")
                spec.domain.height_m = ProvenanceField(
                    value=recommended_height,
                    source=FieldSource.MODEL_RECOMMENDED,
                    status=FieldStatus.AWAITING_CONFIRMATION,
                    confidence=0.6,
                )
            elif d is not None:
                recommended_height = d * 20
                decision.assumptions.append(f"推荐计算域高度 {recommended_height} m")
                spec.domain.height_m = ProvenanceField(
                    value=recommended_height,
                    source=FieldSource.MODEL_RECOMMENDED,
                    status=FieldStatus.AWAITING_CONFIRMATION,
                    confidence=0.6,
                )
            else:
                spec.domain.height_m = ProvenanceField(
                    value=25.0,
                    source=FieldSource.SYSTEM_DEFAULT,
                    status=FieldStatus.AWAITING_CONFIRMATION,
                    confidence=0.3,
                )

        # Default cylinder position if not specified (needed for geometry feasibility checks)
        if spec.cylinder.type == "cylinder":
            domain_l = spec.domain.length_m.value
            domain_h = spec.domain.height_m.value
            if domain_l is not None and domain_l > 0 and not spec.cylinder.center_x_m.is_resolved():
                default_cx = domain_l / 2.0
                decision.assumptions.append(f"圆柱圆心默认位于域水平中心 x={default_cx} m")
                spec.cylinder.center_x_m = ProvenanceField(
                    value=default_cx,
                    source=FieldSource.MODEL_RECOMMENDED,
                    status=FieldStatus.AWAITING_CONFIRMATION,
                    confidence=0.6,
                )
            if domain_h is not None and domain_h > 0 and not spec.cylinder.center_y_m.is_resolved():
                default_cy = domain_h / 2.0
                decision.assumptions.append(f"圆柱圆心默认位于域垂直中心 y={default_cy} m")
                spec.cylinder.center_y_m = ProvenanceField(
                    value=default_cy,
                    source=FieldSource.MODEL_RECOMMENDED,
                    status=FieldStatus.AWAITING_CONFIRMATION,
                    confidence=0.6,
                )

        return stage

    def _pass5_observables(
        self,
        spec: CylinderFlow2DExperimentSpecV1,
        user_text: str,
        decision: DecisionSummary,
    ) -> PipelineStageResult:
        """Extract user-explicit observables, then recommend."""
        stage = PipelineStageResult(stage_name="observable_extraction")

        from fluid_scientist.cylinder_flow_2d.observable import (
            CylinderFlow2DObservableExtractor,
            CylinderFlow2DObservableRecommender,
            CylinderFlow2DObservableValidator,
        )

        # Extract user-explicit
        extractor = CylinderFlow2DObservableExtractor()
        user_observables = extractor.extract(user_text)
        spec.observables.extend(user_observables)

        # Recommend if empty or incomplete
        recommender = CylinderFlow2DObservableRecommender()
        recommended = recommender.recommend(spec)
        # Only add recommendations that don't duplicate user-explicit ones
        existing_types = {obs.type for obs in spec.observables}
        for rec in recommended:
            if rec.type not in existing_types:
                spec.observables.append(rec)

        # Validate
        validator = CylinderFlow2DObservableValidator()
        spec.observables = validator.validate(spec.observables)

        return stage

    def _pass5b_analysis_goals(
        self,
        spec: CylinderFlow2DExperimentSpecV1,
        decision: DecisionSummary,
    ) -> PipelineStageResult:
        """Generate analysis goals from experiment semantics."""
        stage = PipelineStageResult(stage_name="analysis_goal_generation")

        from fluid_scientist.cylinder_flow_2d.analysis_goals import (
            CylinderFlow2DAnalysisGoalBuilder,
        )
        builder = CylinderFlow2DAnalysisGoalBuilder()
        goals = builder.build(spec)
        spec.analysis_goals.extend(goals)

        return stage

    # -----------------------------------------------------------------------
    # Pass 6: Critic
    # -----------------------------------------------------------------------

    def _pass6_critic(
        self,
        spec: CylinderFlow2DExperimentSpecV1,
        user_text: str,
        decision: DecisionSummary,
    ) -> PipelineStageResult:
        """Independent critic review with auto-repair."""
        stage = PipelineStageResult(stage_name="critic")

        from fluid_scientist.cylinder_flow_2d.critic import (
            CylinderFlow2DCritic,
            CylinderFlow2DCoverageChecker,
        )

        critic = CylinderFlow2DCritic()
        critic_result = critic.review(spec, user_text)

        # Coverage check
        coverage = CylinderFlow2DCoverageChecker()
        gaps = coverage.check(spec, user_text)

        if gaps:
            for gap in gaps:
                stage.warnings.append(gap["message"])
                decision.unresolved_items.append(gap["code"])

        if critic_result.issues_found:
            for issue in critic_result.issues_found:
                if issue["severity"] == "blocking":
                    stage.errors.append(issue["message"])
                else:
                    stage.warnings.append(issue["message"])

        if critic_result.auto_repairs_applied:
            for repair in critic_result.auto_repairs_applied:
                decision.derived_values.append(f"自动修复: {repair['what']}")

        # Record rejected interpretations
        if not critic_result.passed:
            decision.rejected_interpretations.append("Critic发现未解决的问题")

        return stage

    # -----------------------------------------------------------------------
    # Helper methods for text extraction
    # -----------------------------------------------------------------------

    # Shared number pattern supporting scientific notation
    _NUM_PATTERN = r"(\d+\.?\d*(?:[eE][+-]?\d+)?)"

    # Unit conversion table (to meters)
    _UNIT_TO_METERS = {
        "mm": 0.001, "毫米": 0.001, "millimeter": 0.001,
        "cm": 0.01, "厘米": 0.01, "centimeter": 0.01,
        "dm": 0.1, "分米": 0.1, "decimeter": 0.1,
        "m": 1.0, "米": 1.0, "meter": 1.0,
    }

    @staticmethod
    def _chinese_num_to_float(text: str) -> float | None:
        """Convert Chinese numeral string to float. Supports 0-9999."""
        cn_map = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
                  "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
                  "百": 100, "千": 1000}
        # Handle simple cases: 十五 = 15, 二十 = 20, 十 = 10, 一百 = 100
        if not text or not all(c in cn_map for c in text):
            # Try mixed like "一百二十三"
            pass
        if text == "十":
            return 10.0
        if text.startswith("十"):
            # 十五 = 15, 十二 = 12
            return 10.0 + float(cn_map.get(text[1], 0))
        if "十" in text:
            parts = text.split("十")
            tens = cn_map.get(parts[0], 1) if parts[0] else 1
            ones = cn_map.get(parts[1], 0) if len(parts) > 1 and parts[1] else 0
            return float(tens * 10 + ones)
        if "百" in text:
            parts = text.split("百")
            hundreds = cn_map.get(parts[0], 1) if parts[0] else 1
            remainder = parts[1] if len(parts) > 1 else ""
            if not remainder:
                return float(hundreds * 100)
            if "十" in remainder:
                rparts = remainder.split("十")
                tens = cn_map.get(rparts[0], 1) if rparts[0] else 1
                ones = cn_map.get(rparts[1], 0) if len(rparts) > 1 and rparts[1] else 0
                return float(hundreds * 100 + tens * 10 + ones)
            return float(hundreds * 100 + cn_map.get(remainder, 0))
        # Pure digit characters
        total = 0
        for c in text:
            if c in cn_map:
                total = total * 10 + cn_map[c]
            else:
                return None
        return float(total) if total > 0 else None

    @classmethod
    def _preprocess_chinese_numbers(cls, text: str) -> str:
        """Replace Chinese numerals with Arabic numerals in text."""
        # Pattern to find Chinese number sequences
        cn_chars = "零〇一二三四五六七八九十百千万两"
        pattern = re.compile(f"[{cn_chars}]+")

        def replacer(m):
            cn_str = m.group(0)
            result = cls._chinese_num_to_float(cn_str)
            if result is not None:
                # Preserve integer vs float
                if result == int(result):
                    return str(int(result))
                return str(result)
            return cn_str

        return pattern.sub(replacer, text)

    @classmethod
    def _extract_value_with_unit(cls, text: str, patterns: list[str], convert_unit: bool = True) -> float | None:
        """Extract a numeric value with optional unit conversion.

        Patterns should use (\\d+\\.?\\d*(?:[eE][+-]?\\d+)?) for the number group
        and a second optional group for the unit.
        """
        # Preprocess Chinese numbers
        text = cls._preprocess_chinese_numbers(text)

        for p in patterns:
            m = re.search(p, text)
            if m:
                val = float(m.group(1))
                # Try to extract unit from group(2) if present
                if len(m.groups()) > 1 and m.group(2):
                    unit_str = m.group(2).strip().lower()
                    if convert_unit and unit_str in cls._UNIT_TO_METERS:
                        val *= cls._UNIT_TO_METERS[unit_str]
                return val
        return None

    def _extract_radius(self, text: str) -> float | None:
        """Extract radius from text: R=0.1, 半径R=0.1, 半径0.1, radius=0.1, etc."""
        text = self._preprocess_chinese_numbers(text)
        _NUM = self._NUM_PATTERN
        _UNIT = r"(mm|cm|dm|m|毫米|厘米|分米|米)?"
        patterns = [
            # "半径R = 0.1 m" or "半径 R = 0.1 m" (Chinese + variable name)
            rf"半径\s*[Rr]\s*=\s*{_NUM}\s*{_UNIT}",
            # "圆柱半径R = 0.1 m"
            rf"圆柱.*?半径\s*[Rr]?\s*=\s*{_NUM}\s*{_UNIT}",
            # "半径R=0.1" without space
            rf"半径\s*[Rr]\s*=\s*{_NUM}",
            # "R = 0.1 m" — but NOT "Re = 200" (negative lookahead for 'e' after R)
            rf"(?<![a-zA-Z])[Rr]\s*=\s*{_NUM}\s*{_UNIT}",
            # "半径 = 0.1 m" or "半径0.1 m"
            rf"半径\s*[=为是]?\s*{_NUM}\s*{_UNIT}",
            # "radius = 0.1 m" or "radius 0.1 m" (without =)
            rf"[Rr]adius\s*[=:]?\s*{_NUM}\s*{_UNIT}",
            # "半径 0.1"
            rf"半径\s*{_NUM}\s*{_UNIT}",
        ]
        for p in patterns:
            m = re.search(p, text)
            if m:
                val = float(m.group(1))
                # Unit conversion
                if len(m.groups()) > 1 and m.group(2):
                    unit = m.group(2).strip().lower()
                    if unit in self._UNIT_TO_METERS:
                        val *= self._UNIT_TO_METERS[unit]
                # Sanity check: radius should be small (not 200 from Re)
                if val > 10:
                    continue
                return val
        return None

    def _extract_diameter(self, text: str) -> float | None:
        """Extract diameter from text: D=0.2, 直径0.2, diameter=0.2, etc."""
        text = self._preprocess_chinese_numbers(text)
        _NUM = self._NUM_PATTERN
        _UNIT = r"(mm|cm|dm|m|毫米|厘米|分米|米)?"
        patterns = [
            rf"[Dd]\s*=\s*{_NUM}\s*{_UNIT}",
            rf"直径\s*[=为是]?\s*{_NUM}\s*{_UNIT}",
            rf"[Dd]iameter\s*[=:]?\s*{_NUM}\s*{_UNIT}",
        ]
        for p in patterns:
            m = re.search(p, text)
            if m:
                val = float(m.group(1))
                # Unit conversion
                if len(m.groups()) > 1 and m.group(2):
                    unit = m.group(2).strip().lower()
                    if unit in self._UNIT_TO_METERS:
                        val *= self._UNIT_TO_METERS[unit]
                # Sanity check: diameter should be reasonable
                if val > 50:
                    continue
                return val
        return None

    def _extract_domain(self, text: str) -> dict[str, float]:
        """Extract domain dimensions from text."""
        text = self._preprocess_chinese_numbers(text)
        _NUM = self._NUM_PATTERN
        _UNIT = r"(mm|cm|dm|m|毫米|厘米|分米|米)?"
        result: dict[str, float] = {}
        # Length: 通道长8, 域长10, 长度300, length=300, 长300m, 长20米
        patterns_l = [
            rf"通道长\s*{_NUM}\s*{_UNIT}",
            rf"域长\s*{_NUM}\s*{_UNIT}",
            rf"长度\s*[=为是]?\s*{_NUM}\s*{_UNIT}",
            rf"[Ll]ength\s*[=:]?\s*{_NUM}\s*{_UNIT}",
            rf"长\s*{_NUM}\s*{_UNIT}",
        ]
        for p in patterns_l:
            m = re.search(p, text)
            if m:
                val = float(m.group(1))
                if len(m.groups()) > 1 and m.group(2):
                    unit = m.group(2).strip().lower()
                    if unit in self._UNIT_TO_METERS:
                        val *= self._UNIT_TO_METERS[unit]
                result["length"] = val
                break

        # Height: 通道高4, 域高6, 高度25, height=25, 宽4米
        patterns_h = [
            rf"通道高\s*{_NUM}\s*{_UNIT}",
            rf"域高\s*{_NUM}\s*{_UNIT}",
            rf"域\s*高\s*{_NUM}\s*{_UNIT}",
            rf"流场\s*高\s*{_NUM}\s*{_UNIT}",
            rf"宽\s*{_NUM}\s*{_UNIT}",
            rf"计算域.*高\s*{_NUM}\s*{_UNIT}",
            rf"[Hh]eight\s*[=:]?\s*{_NUM}\s*{_UNIT}",
        ]
        for p in patterns_h:
            m = re.search(p, text)
            if m:
                val = float(m.group(1))
                if len(m.groups()) > 1 and m.group(2):
                    unit = m.group(2).strip().lower()
                    if unit in self._UNIT_TO_METERS:
                        val *= self._UNIT_TO_METERS[unit]
                result["height"] = val
                break

        return result

    def _extract_cylinder_position(self, text: str) -> dict[str, float]:
        """Extract cylinder position from text."""
        result: dict[str, float] = {}

        # 圆心(x,y) format — check FIRST (highest priority)
        # Matches: 圆心(5,0.05), 圆心(5, 0.05), 圆心（5，0.05）, center(5,0.05)
        m = re.search(r"圆心\s*[\(（]\s*(\d+\.?\d*)\s*[,，]\s*(\d+\.?\d*)\s*[\)）]", text)
        if m:
            x_val = float(m.group(1))
            y_val = float(m.group(2))
            if 0 < x_val < 10000:
                result["x"] = x_val
            if 0 < y_val < 10000:
                result["y"] = y_val
            return result  # 圆心(x,y) takes priority, skip generic x=/y= patterns

        # x position: x=10, 圆心x=10, 位置x=10
        # Use negative lookbehind to avoid matching 'x' inside words like "next", "exit"
        m = re.search(r"(?<![a-zA-Z])[xX]\s*[=为在]\s*(\d+\.?\d*)\s*m?", text)
        if m:
            val = float(m.group(1))
            if 0 < val < 10000:  # sanity check
                result["x"] = val

        # y position: y=5, 圆心y=5, 高度y=5
        # Use negative lookbehind to avoid matching 'y' inside words like "infty", "only"
        m = re.search(r"(?<![a-zA-Z])[yY]\s*[=为在]\s*(\d+\.?\d*)\s*m?", text)
        if m:
            val = float(m.group(1))
            if 0 < val < 10000:  # sanity check
                result["y"] = val

        return result

    def _extract_wall_distance(self, text: str) -> float | None:
        """Extract wall distance from text: 距壁面2m, 距下壁面2m, 距下壁面 H = 2 m."""
        patterns = [
            # "距下壁面 H = 2 m" — variable name between 壁面 and number
            r"距\s*[下底]*\s*壁面\s*[A-Za-z]?\s*[=为是]?\s*(\d+\.?\d*)\s*m?",
            # "距下壁面2m"
            r"距\s*[下底]*\s*壁面\s*(\d+\.?\d*)\s*m?",
            # "距下壁 2m"
            r"距\s*[下底]*\s*壁\s*(\d+\.?\d*)\s*m?",
            # "距离下壁 2m"
            r"距离\s*[下底]*\s*壁\s*(\d+\.?\d*)\s*m?",
            # "距下壁面H=2m" (no space)
            r"距\s*[下底]*\s*壁面\s*[Hh]\s*=\s*(\d+\.?\d*)\s*m?",
            # "放置在距下壁面 H = 2 m 处"
            r"放置.*?距\s*[下底]*\s*壁面\s*[A-Za-z]?\s*[=为是]?\s*(\d+\.?\d*)\s*m?",
            # "H = 2 m" near 壁面 (within 20 chars)
            r"[Hh]\s*=\s*(\d+\.?\d*)\s*m?(?:.*?壁面|.*?wall)",
            r"(?:壁面|wall).*?[Hh]\s*=\s*(\d+\.?\d*)\s*m?",
        ]
        for p in patterns:
            m = re.search(p, text)
            if m:
                val = float(m.group(1))
                # Sanity check: wall distance should be positive and reasonable
                if 0 < val < 100:
                    return val
        return None

    def _extract_inlet_velocity(self, text: str) -> float | None:
        """Extract inlet velocity from text: U=1.0, U_infty=1.0, 来流速度1.0, 以1 m/s流入, etc."""
        text = self._preprocess_chinese_numbers(text)
        _NUM = self._NUM_PATTERN
        patterns = [
            # U_\infty = 1.0m/s (LaTeX subscript)
            rf"U\s*_?\\?\s*infty\s*=\s*{_NUM}\s*m/s",
            # U_∞ = 1.0m/s (Unicode)
            rf"U\s*_?\s*∞\s*=\s*{_NUM}\s*m/s",
            # U∞ = 1.0m/s
            rf"U∞\s*=\s*{_NUM}\s*m/s",
            # U = 1.0m/s (basic)
            rf"[Uu]\s*=\s*{_NUM}\s*m/s",
            # "恒定速度 U... = 1.0m/s" or "恒定速度1.0m/s"
            rf"恒定速度\s*(?:[Uu]\S*\s*=?\s*)?{_NUM}\s*m/s",
            # "恒定速度...1.0m/s"
            rf"恒[定速]*\s*速度\s*[=为是]?\s*{_NUM}\s*m/s",
            # 来流...速度...1.0m/s
            rf"来流.*?速度\s*[=为是]?\s*{_NUM}\s*m/s",
            rf"来流[速度]*\s*[=为是]?\s*{_NUM}\s*m/s?",
            # 入口速度
            rf"入口[速度]*\s*[=为是]?\s*{_NUM}\s*m/s?",
            # Velocity = 1.0 (with = sign)
            rf"[Vv]elocity\s*=\s*{_NUM}\s*m/s?",
            # "inlet velocity 1.0m/s" or "velocity 1.0m/s" (without = sign)
            rf"inlet\s*velocity\s*{_NUM}\s*m/s",
            rf"velocity\s+{_NUM}\s*m/s",
            # "以1 m/s流入" / "以2 m/s恒速流入"
            rf"以\s*{_NUM}\s*m/s",
            # "1 m/s恒速流入" / "1 m/s流入"
            rf"{_NUM}\s*m/s\s*(?:恒速|流入)",
            # "v=2+..." formulas — extract the mean velocity (first number after =)
            rf"[Vv]\s*=\s*{_NUM}",
            # Generic: any number followed by m/s near "流" keyword
            rf"流[动入]?\s*(?:以|速度|速)?\s*[=为是]?\s*{_NUM}\s*m/s",
        ]
        for p in patterns:
            m = re.search(p, text)
            if m:
                return float(m.group(1))
        return None

    def _extract_density(self, text: str) -> float | None:
        """Extract fluid density: 密度1.225, density=1.225, ρ=1.225."""
        text = self._preprocess_chinese_numbers(text)
        _NUM = self._NUM_PATTERN
        patterns = [
            rf"密度\s*[=为是]?\s*{_NUM}\s*kg/m3?",
            rf"[Dd]ensity\s*=\s*{_NUM}\s*kg/m3?",
            rf"ρ\s*=\s*{_NUM}\s*kg/m3?",
            rf"密度\s*[=为是]?\s*{_NUM}",
        ]
        for p in patterns:
            m = re.search(p, text)
            if m:
                return float(m.group(1))
        return None

    def _extract_viscosity(self, text: str) -> float | None:
        """Extract kinematic viscosity: 运动粘度/黏度1.5e-5, viscosity=1.5e-5, ν=1.5e-5."""
        text = self._preprocess_chinese_numbers(text)
        _NUM = self._NUM_PATTERN
        _VISC_UNIT = r"(?:平方米每秒|m2/s|m\^2/s|m²/s|s\b)?"
        patterns = [
            # Support both 粘度 and 黏度
            rf"运动[粘黏]度\s*[=为是]?\s*{_NUM}\s*{_VISC_UNIT}",
            rf"运动[粘黏]度\s*[=为是]?\s*{_NUM}",
            rf"[Vv]iscosity\s*=\s*{_NUM}\s*{_VISC_UNIT}",
            rf"ν\s*=\s*{_NUM}\s*{_VISC_UNIT}",
            rf"运动[粘黏]度\s*[=为是]?\s*{_NUM}\s*{_VISC_UNIT}",
            # Also support bare "1e-5 m²/s" without keyword
            rf"(?<![a-zA-Z\d]){_NUM}\s*(?:平方米每秒|m2/s|m\^2/s|m²/s)",
            rf"运动[粘黏]度\s*[=为是]?\s*{_NUM}\s*m",
        ]
        for p in patterns:
            m = re.search(p, text)
            if m:
                return float(m.group(1))
        return None

    def _extract_angular_velocity(self, text: str) -> float | None:
        """Extract angular velocity: 角速度10, angular velocity=10, ω=10."""
        patterns = [
            r"角速度\s*[=为是]?\s*(\d+\.?\d*)\s*rad/s?",
            r"角速度\s*[=为是]?\s*(\d+\.?\d*)",
            r"[Aa]ngular\s*[Vv]elocity\s*=\s*(\d+\.?\d*)\s*rad/s?",
            r"ω\s*=\s*(\d+\.?\d*)\s*rad/s?",
            r"以角速度\s*(\d+\.?\d*)",
        ]
        for p in patterns:
            m = re.search(p, text)
            if m:
                return float(m.group(1))
        return None

    def _extract_bump(self, text: str) -> dict[str, Any] | None:
        """Extract bottom bump/obstacle profile from text.

        Only detects actual bump shapes (cosine_bell, half_sine, gaussian).
        Does NOT match triangle, rectangle, or other polygon obstacles.
        Those are handled by their own extraction methods.
        """
        text_lower = text.lower()

        # Exclude triangle and rectangle — they have their own extraction
        triangle_kws = ["三角", "triangle", "triangular", "wedge"]
        rectangle_kws = ["矩形", "rectangle", "rectangular"]
        if any(kw in text_lower for kw in [k.lower() for k in triangle_kws + rectangle_kws]):
            return None

        # Only match actual bump-related keywords
        bump_keywords = [
            "凸起", "bump", "底面凸起", "壁面凸起",
            "余弦", "cosine", "正弦", "sine", "高斯", "gaussian",
            "rib", "小山包", "bell",
        ]
        if not any(kw in text_lower for kw in [k.lower() for k in bump_keywords]):
            return None

        result: dict[str, Any] = {}

        # Extract bump height: 凸起高0.3, 凸起高度0.3, 高0.05m
        h_patterns = [
            r"凸起高\s*(\d+\.?\d*)\s*m?",
            r"凸起高度\s*[=为是]?\s*(\d+\.?\d*)\s*m?",
            r"凸起.*?高\s*(\d+\.?\d*)\s*m?",
            # "高 0.05 m" (generic height near obstacle keyword)
            r"(?:障碍|凸起|贴附).*?高\s*(\d+\.?\d*)\s*m?",
            r"高\s*(\d+\.?\d*)\s*m?(?:.*?(?:障碍|凸起|宽))",
            r"bump.*?height\s*[=]?\s*(\d+\.?\d*)\s*m?",
        ]
        for p in h_patterns:
            m = re.search(p, text_lower)
            if m:
                result["height"] = float(m.group(1))
                break

        # Extract bump width: 凸起宽1, 凸起宽度1, 宽0.1m
        w_patterns = [
            r"凸起宽\s*(\d+\.?\d*)\s*m?",
            r"凸起宽度\s*[=为是]?\s*(\d+\.?\d*)\s*m?",
            r"凸起.*?宽\s*(\d+\.?\d*)\s*m?",
            # "宽 0.1 m" (generic width near obstacle keyword)
            r"(?:障碍|凸起|贴附).*?宽\s*(\d+\.?\d*)\s*m?",
            r"宽\s*(\d+\.?\d*)\s*m?(?:.*?(?:障碍|凸起))",
            r"bump.*?width\s*[=]?\s*(\d+\.?\d*)\s*m?",
        ]
        for p in w_patterns:
            m = re.search(p, text_lower)
            if m:
                result["width"] = float(m.group(1))
                break

        # Extract bump center_x if specified: 凸起位于x=4, 凸起中心x=4, 圆柱正下方
        cx_patterns = [
            r"凸起.*?[xX]\s*[=为在]\s*(\d+\.?\d*)\s*m?",
            r"凸起.*?位于.*?[xX]\s*[=为]?\s*(\d+\.?\d*)\s*m?",
            r"凸起.*?中心.*?[xX]\s*[=为]?\s*(\d+\.?\d*)\s*m?",
            r"[xX]\s*[=为]\s*(\d+\.?\d*)\s*m?.*?凸起",
        ]
        for p in cx_patterns:
            m = re.search(p, text)
            if m:
                result["center_x"] = float(m.group(1))
                break

        # Detect alignment with cylinder — "位于圆柱正下方", "圆柱正下方", "圆柱下方"
        if any(kw in text for kw in ["圆柱正下方", "圆柱下方", "位于圆柱正下方",
                                      "位于圆柱下方", "正对圆柱下方",
                                      "below cylinder", "directly below cylinder",
                                      "beneath cylinder"]):
            result["aligned_below_cylinder"] = True

        # Profile type detection
        if "cosine" in text_lower or "余弦" in text:
            result["profile_type"] = "cosine_bell"
        elif "sine" in text_lower or "正弦" in text:
            result["profile_type"] = "half_sine"
        elif "gaussian" in text_lower or "高斯" in text:
            result["profile_type"] = "gaussian"

        return result if result else None

    def _extract_triangle(self, text: str) -> dict[str, Any] | None:
        """Extract triangle obstacle from text.

        Detects: 三角形, 三角障碍物, 三角凸起, triangle, triangular obstacle, triangular bump
        Extracts: height, base_width, center_x, apex_direction, attached_boundary
        NEVER maps to cosine_bell.

        Supports both Chinese and English descriptions. For English text,
        uses an extended proximity window and full-text fallback search
        to handle verbose English descriptions where dimensions may be
        far from the keyword.
        """
        text_lower = text.lower()
        triangle_keywords = [
            "三角", "triangle", "triangular",
        ]
        tri_kw_found = None
        tri_pos = -1
        for kw in triangle_keywords:
            pos = text_lower.find(kw.lower())
            if pos >= 0:
                tri_kw_found = kw
                tri_pos = pos
                break
        if tri_pos < 0:
            return None

        result: dict[str, Any] = {}

        # Detect language: if text is predominantly English, use larger window
        chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
        is_english = chinese_chars < len(text) * 0.3

        # Extract a text window around the triangle keyword to avoid
        # matching domain dimensions (e.g. "宽5米" from domain description)
        # Use larger window for English (English descriptions are more verbose)
        window_before = 80 if is_english else 60
        window_after = 120 if is_english else 40
        window_start = max(0, tri_pos - window_before)
        window_end = min(len(text), tri_pos + window_after)
        window = text[window_start:window_end]
        window_lower = window.lower()

        # Also prepare full text for fallback search
        full_lower = text_lower

        # Extract triangle height
        h_patterns = [
            r"(?:高|高度)\s*[=为是]?\s*(\d+\.?\d*)\s*[m米]?",
            r"height\s*[=:]?\s*(\d+\.?\d*)\s*m?",
            r"高\s*(\d+\.?\d*)\s*m",
        ]
        for p in h_patterns:
            for m in re.finditer(p, window):
                val = float(m.group(1))
                if val < 2.0:  # sanity: obstacle height should be small, not domain height
                    result["height"] = val
                    break
            if "height" in result:
                break

        # Full-text fallback for English height patterns
        if "height" not in result and is_english:
            for p in h_patterns[1:]:
                for m in re.finditer(p, full_lower):
                    val = float(m.group(1))
                    if val < 2.0:
                        result["height"] = val
                        break
                if "height" in result:
                    break

        # Extract triangle base width
        w_patterns = [
            r"(?:底宽|底边|宽度|宽)\s*[=为是]?\s*(\d+\.?\d*)\s*[m米]?",
            r"base.*?width\s*[=:]?\s*(\d+\.?\d*)\s*m?",
            r"width\s*[=:]?\s*(\d+\.?\d*)\s*m?",
        ]
        for p in w_patterns:
            for m in re.finditer(p, window):
                val = float(m.group(1))
                if val < 2.0:  # sanity: obstacle width should be small, not domain width
                    result["base_width"] = val
                    break
            if "base_width" in result:
                break

        # Full-text fallback for English width patterns
        if "base_width" not in result and is_english:
            for p in w_patterns[1:]:
                for m in re.finditer(p, full_lower):
                    val = float(m.group(1))
                    if val < 2.0:
                        result["base_width"] = val
                        break
                if "base_width" in result:
                    break

        # Extract triangle center_x from full text
        cx_patterns = [
            r"三角.*?[xX]\s*[=为在]\s*(\d+\.?\d*)\s*m?",
            r"三角.*?位于.*?[xX]\s*[=为]?\s*(\d+\.?\d*)\s*m?",
        ]
        for p in cx_patterns:
            m = re.search(p, text)
            if m:
                result["center_x"] = float(m.group(1))
                break

        # Detect apex direction
        if "尖端向上" in text or "尖端朝上" in text or "顶点向上" in text:
            result["apex_direction"] = "up"
        elif "尖端向下" in text or "尖端朝下" in text or "顶点向下" in text:
            result["apex_direction"] = "down"
        else:
            result["apex_direction"] = "up"  # default assumption

        # Detect attached boundary
        if "下壁面" in window or "底" in window:
            result["attached_boundary"] = "bottom_wall"
        elif "上壁面" in window:
            result["attached_boundary"] = "top_wall"

        # Detect alignment with cylinder
        if "圆柱正下方" in text or "圆柱下方" in text:
            result["aligned_below_cylinder"] = True

        return result if result else None

    def _extract_rectangle(
        self,
        text: str,
        spec: CylinderFlow2DExperimentSpecV1,
    ) -> dict[str, Any] | None:
        """Extract rectangle obstacle from text.

        Detects: 矩形, 矩形凸起, 矩形障碍, 长方形, rectangle, rectangular obstacle
        Extracts: height, width, center_x, aligned_below_cylinder

        Supports both Chinese and English descriptions. Uses domain-aware
        sanity checks to avoid matching domain dimensions.
        """
        text_lower = text.lower()
        rectangle_keywords = [
            "矩形", "长方形", "rectangle", "rectangular",
        ]
        rect_kw_found = None
        rect_pos = -1
        for kw in rectangle_keywords:
            pos = text_lower.find(kw.lower())
            if pos >= 0:
                rect_kw_found = kw
                rect_pos = pos
                break
        if rect_pos < 0:
            return None

        result: dict[str, Any] = {}

        # Detect language
        chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
        is_english = chinese_chars < len(text) * 0.3

        # Extract a text window around the rectangle keyword
        # Use a tight window_before to avoid matching domain dimensions
        # (e.g. "宽8米" from "长20米，宽8米" that precedes the rectangle description)
        window_before = 80 if is_english else 25
        window_after = 120 if is_english else 60
        window_start = max(0, rect_pos - window_before)
        window_end = min(len(text), rect_pos + window_after)
        window = text[window_start:window_end]

        # Domain dimensions for sanity check
        domain_h = spec.domain.height_m.value if spec.domain.height_m.is_resolved() else 10.0
        domain_l = spec.domain.length_m.value if spec.domain.length_m.is_resolved() else 20.0

        # Extract rectangle height: 高2m, 高度2, 高=2, height 2
        h_patterns = [
            r"(?:高|高度)\s*[=为是]?\s*(\d+\.?\d*)\s*[m米]?",
            r"height\s*[=:]?\s*(\d+\.?\d*)\s*m?",
            r"高\s*(\d+\.?\d*)\s*[m米]",
        ]
        for p in h_patterns:
            for m in re.finditer(p, window):
                val = float(m.group(1))
                # Sanity: obstacle height must be less than domain height
                if val < domain_h:
                    result["height"] = val
                    break
            if "height" in result:
                break

        # Full-text fallback for English
        if "height" not in result and is_english:
            for p in h_patterns[1:]:
                for m in re.finditer(p, text_lower):
                    val = float(m.group(1))
                    if val < domain_h:
                        result["height"] = val
                        break
                if "height" in result:
                    break

        # Extract rectangle width: 宽4m, 宽度4, 宽=4, width 4
        w_patterns = [
            r"(?:宽|宽度)\s*[=为是]?\s*(\d+\.?\d*)\s*[m米]?",
            r"width\s*[=:]?\s*(\d+\.?\d*)\s*m?",
        ]
        for p in w_patterns:
            for m in re.finditer(p, window):
                val = float(m.group(1))
                # Sanity: obstacle width must be less than domain length
                if val < domain_l:
                    result["width"] = val
                    break
            if "width" in result:
                break

        # Full-text fallback for English
        if "width" not in result and is_english:
            for p in w_patterns[1:]:
                for m in re.finditer(p, text_lower):
                    val = float(m.group(1))
                    if val < domain_l:
                        result["width"] = val
                        break
                if "width" in result:
                    break

        # Extract rectangle center_x
        cx_patterns = [
            r"矩形.*?[xX]\s*[=为在]\s*(\d+\.?\d*)\s*m?",
            r"矩形.*?位于.*?[xX]\s*[=为]?\s*(\d+\.?\d*)\s*m?",
            r"rectangle.*?[xX]\s*[=:]?\s*(\d+\.?\d*)\s*m?",
        ]
        for p in cx_patterns:
            m = re.search(p, text, re.IGNORECASE)
            if m:
                result["center_x"] = float(m.group(1))
                break

        # Detect alignment with cylinder
        if "圆柱正下方" in text or "圆柱下方" in text:
            result["aligned_below_cylinder"] = True

        return result if result else None

    def _extract_end_time(self, text: str) -> float | None:
        """Extract simulation end time: 仿真时间2秒, 仿真时间设为15秒, end_time=2."""
        text = self._preprocess_chinese_numbers(text)
        _NUM = self._NUM_PATTERN
        patterns = [
            rf"仿真时间\s*(?:设为|设置为|为|是|=)?\s*{_NUM}\s*秒?",
            rf"仿真时间\s*(?:设为|设置为|为|是|=)?\s*{_NUM}\s*s\b",
            rf"模拟时间\s*(?:设为|设置为|为|是|=)?\s*{_NUM}\s*秒?",
            rf"计算时间\s*(?:设为|设置为|为|是|=)?\s*{_NUM}\s*秒?",
            rf"[Ee]nd[_\s]*[Tt]ime\s*=\s*{_NUM}",
            rf"运行时间\s*(?:设为|设置为|为|是|=)?\s*{_NUM}\s*秒?",
        ]
        for p in patterns:
            m = re.search(p, text)
            if m:
                return float(m.group(1))
        return None

    def _extract_reynolds(self, text: str) -> float | None:
        """Extract Reynolds number: Re=200, Re = 200, 雷诺数200, Reynolds 200."""
        patterns = [
            r"(?<![a-zA-Z])[Rr]e\s*=\s*(\d+\.?\d*)",
            r"雷诺数\s*[=为是]?\s*(\d+\.?\d*)",
            r"[Rr]eynolds\s*(?:number)?\s*[=:]?\s*(\d+\.?\d*)",
            r"(?<![a-zA-Z])[Rr]e\s+(\d+\.?\d*)",  # "Re 200" with space
        ]
        for p in patterns:
            m = re.search(p, text)
            if m:
                val = float(m.group(1))
                if 0.1 < val < 1e8:  # sanity check
                    return val
        return None

    def _compute_confidence(self, spec: CylinderFlow2DExperimentSpecV1) -> float:
        """Compute overall confidence based on resolved fields."""
        total = 0
        resolved = 0

        # Cylinder
        total += 3
        if spec.cylinder.type == "cylinder":
            resolved += 1
        if spec.get_cylinder_radius() is not None:
            resolved += 1
        if spec.cylinder.center_x_m.is_resolved():
            resolved += 1

        # Domain
        total += 2
        if spec.domain.length_m.is_resolved():
            resolved += 1
        if spec.domain.height_m.is_resolved():
            resolved += 1

        # Boundaries
        total += 4
        if spec.boundaries.left.semantic_type is not None:
            resolved += 1
        if spec.boundaries.right.semantic_type is not None:
            resolved += 1
        if spec.boundaries.top.semantic_type is not None:
            resolved += 1
        if spec.boundaries.bottom_flat.semantic_type is not None:
            resolved += 1

        # Fluid
        total += 2
        if spec.fluid.type.is_resolved():
            resolved += 1
        if spec.fluid.kinematic_viscosity_m2_s.is_resolved():
            resolved += 1

        # Observables and goals
        total += 2
        if len(spec.observables) > 0:
            resolved += 1
        if len(spec.analysis_goals) > 0:
            resolved += 1

        return resolved / total if total > 0 else 0.0


__all__ = [
    "CylinderFlow2DV1Pipeline",
    "PipelineRunResult",
    "PipelineStageResult",
]
