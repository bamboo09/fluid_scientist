"""CylinderFlow2D scene router — detects cylinder flow from user text.

Implements Section 2.2 of the plan. Determines whether user input
should enter the dedicated CylinderFlow2D pipeline.

Routing returns MUST include:
  pipeline_id: "cylinder-flow-2d-v1"
  schema_name: "CylinderFlow2DExperimentSpecV1"
  pipeline_version: "1.0"
  pipeline_stage: "DRAFT_NORMALIZED"

When the scene is matched, the system MUST NOT fall back to:
  - legacy research draft generator
  - generic case-plan generator
  - old /api/research-sessions draft generation logic
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SceneRouteResult:
    """Result of scene routing."""

    matched: bool
    pipeline_id: str = "cylinder-flow-2d-v1"
    schema_name: str = "CylinderFlow2DExperimentSpecV1"
    pipeline_version: str = "1.0"
    pipeline_stage: str = "DRAFT_NORMALIZED"
    reason: str = ""
    confidence: float = 0.0
    not_family_reason: str = ""


class CylinderFlow2DSceneRouter:
    """Routes user input to the CylinderFlow2D pipeline.

    A scene is matched when ALL of the following are true:
    - The text mentions 2D or quasi-2D flow
    - The text mentions a cylinder, circular body, or circular obstacle
    - The text describes flow past or around the cylinder

    The router is deliberately conservative — it only routes to the
    dedicated pipeline when there is clear evidence of a cylinder flow
    scenario. Otherwise it returns NOT_CYLINDER_FLOW_FAMILY.
    """

    CYLINDER_KEYWORDS = [
        "圆柱", "圆形障碍物", "圆形物体",
        "cylinder", "circular body", "circular obstacle",
        "凸起", "bump", "底面凸起", "壁面凸起",
    ]

    FLOW_KEYWORDS = [
        "绕流", "流过", "经过", "来流", "流动",
        "flow past", "flow around", "flow over",
        "cross-flow", "crossflow",
        "通道流", "channel flow", "管道流",
    ]

    DIMENSION_KEYWORDS = [
        "二维", "2D", "2d", "2 D", "2 d",
        "准二维", "2d simulation", "2d case",
    ]

    def route(self, user_text: str) -> SceneRouteResult:
        """Determine if the input should enter the CylinderFlow2D pipeline.

        Args:
            user_text: The user's natural language input.

        Returns:
            SceneRouteResult with matched=True if the scene is a cylinder flow.
        """
        text_lower = user_text.lower()

        # Check for cylinder keywords
        has_cylinder = any(
            kw.lower() in text_lower for kw in self.CYLINDER_KEYWORDS
        )

        # Check for flow keywords
        has_flow = any(
            kw.lower() in text_lower for kw in self.FLOW_KEYWORDS
        )

        # Check for dimension keywords
        has_2d = any(
            kw.lower() in text_lower for kw in self.DIMENSION_KEYWORDS
        )

        # Also accept if cylinder is mentioned with general CFD terms
        has_cfd_context = any(
            kw in text_lower for kw in [
                "cfd", "openfoam", "仿真", "模拟", "计算",
                "simulation", "mesh", "网格", "边界", "boundary",
                "雷诺", "reynolds", "re",
            ]
        )

        # Match logic:
        # Strong match: cylinder + flow (regardless of 2D)
        # Medium match: cylinder + CFD context
        # Weak match: cylinder only (still route, 2D is default)
        if has_cylinder and has_flow:
            confidence = 0.95
            reason = "检测到圆柱绕流关键词"
        elif has_cylinder and has_cfd_context:
            confidence = 0.85
            reason = "检测到圆柱与CFD上下文"
        elif has_cylinder:
            confidence = 0.70
            reason = "检测到圆柱关键词"
        else:
            return SceneRouteResult(
                matched=False,
                reason="未检测到圆柱相关关键词",
                not_family_reason="NOT_CYLINDER_FLOW_FAMILY",
            )

        # Check for explicit non-2D indicators
        non_2d_indicators = ["三维", "3D", "3d", "3 D", "three dimensional"]
        has_non_2d = any(ind.lower() in text_lower for ind in non_2d_indicators)
        if has_non_2d:
            return SceneRouteResult(
                matched=False,
                reason="用户明确指定了三维，不属于二维圆柱绕流实验族",
                not_family_reason="NOT_CYLINDER_FLOW_FAMILY",
            )

        return SceneRouteResult(
            matched=True,
            confidence=confidence,
            reason=reason,
        )


__all__ = [
    "CylinderFlow2DSceneRouter",
    "SceneRouteResult",
]
