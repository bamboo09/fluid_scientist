"""Analysis-goal generation for CylinderFlow2D experiments.

The :class:`CylinderFlow2DAnalysisGoalBuilder` produces a list of
:class:`AnalysisGoalSpec` entries that describe what the simulation
should reveal.  Goals are derived from the experiment configuration
(cylinder presence, bottom profile, flow topology, time mode) and from
the observables the user has explicitly requested.

All generated goals carry ``source = MODEL_RECOMMENDED`` and
``status = AWAITING_CONFIRMATION``.  The goal list is **never** empty.
"""

from __future__ import annotations

from fluid_scientist.cylinder_flow_2d.models import (
    CylinderFlow2DExperimentSpecV1,
    AnalysisGoalSpec,
    FieldSource,
    FieldStatus,
    ObservableType,
)


class CylinderFlow2DAnalysisGoalBuilder:
    """Build analysis goals for a CylinderFlow2D experiment spec.

    Goal generation considers five inputs:

    1. **Cylinder** -- always present in this module; produces three base
       goals covering flow separation, forces, and wake recovery.
    2. **Bottom profile** -- when enabled, adds a goal about the profile's
       influence on near-cylinder flow.
    3. **Flow topology** -- reserved for future goal conditioning (the
       flow topology dict is available on the spec for downstream use).
    4. **Time mode** -- transient simulations add a goal about unsteady
       force fluctuations and periodic vortex shedding.
    5. **User observables** -- when the user requests section mean
       velocity, a dedicated goal is added.
    """

    def build(self, spec: CylinderFlow2DExperimentSpecV1) -> list[AnalysisGoalSpec]:
        """Return a non-empty list of analysis goals for *spec*.

        The returned goals always include the three base cylinder-scene
        goals.  Additional goals are appended conditionally based on the
        spec's bottom-profile flag, time mode, and user-requested
        observables.
        """
        goals: list[AnalysisGoalSpec] = []

        # --- Base cylinder scene goals (always present) -------------------
        goals.append(AnalysisGoalSpec(
            id="cylinder_separation",
            description="分析圆柱周围的流动分离及尾迹结构。",
            related_observables=["vorticity_field", "streamlines"],
            source=FieldSource.MODEL_RECOMMENDED,
            status=FieldStatus.AWAITING_CONFIRMATION,
            confidence=0.7,
        ))
        goals.append(AnalysisGoalSpec(
            id="cylinder_forces",
            description="评估圆柱阻力和升力特征。",
            related_observables=["cylinder_drag", "cylinder_lift"],
            source=FieldSource.MODEL_RECOMMENDED,
            status=FieldStatus.AWAITING_CONFIRMATION,
            confidence=0.7,
        ))
        goals.append(AnalysisGoalSpec(
            id="cylinder_wake_recovery",
            description="分析圆柱下游速度亏损及恢复过程。",
            related_observables=["velocity_magnitude_field", "point_velocity"],
            source=FieldSource.MODEL_RECOMMENDED,
            status=FieldStatus.AWAITING_CONFIRMATION,
            confidence=0.7,
        ))

        # --- Section mean velocity goal (from user observables) ----------
        if self._has_observable(spec, ObservableType.SECTION_MEAN_VELOCITY):
            goals.append(AnalysisGoalSpec(
                id="section_mean_velocity",
                description="计算指定截面的平均流速，并分析其随时间的变化和稳定性。",
                related_observables=["section_mean_velocity"],
                source=FieldSource.MODEL_RECOMMENDED,
                status=FieldStatus.AWAITING_CONFIRMATION,
                confidence=0.7,
            ))

        # --- Bottom profile goal ------------------------------------------
        if spec.has_bottom_profile:
            goals.append(AnalysisGoalSpec(
                id="bottom_profile_effect",
                description="分析底部轮廓对圆柱附近流动、回流区和速度分布的影响。",
                related_observables=["velocity_magnitude_field", "vorticity_field"],
                source=FieldSource.MODEL_RECOMMENDED,
                status=FieldStatus.AWAITING_CONFIRMATION,
                confidence=0.7,
            ))

        # --- Transient goal ----------------------------------------------
        if spec.is_transient:
            goals.append(AnalysisGoalSpec(
                id="transient_vortex_shedding",
                description="分析圆柱升阻力波动及周期性涡脱落特征。",
                related_observables=["drag_lift_time_series", "wake_shedding_frequency"],
                source=FieldSource.MODEL_RECOMMENDED,
                status=FieldStatus.AWAITING_CONFIRMATION,
                confidence=0.7,
            ))

        # NEVER empty -- the three base goals already guarantee this, but
        # guard against future structural changes that might remove them.
        if not goals:
            goals.append(AnalysisGoalSpec(
                id="basic_flow_analysis",
                description="分析圆柱周围的流动分离及尾迹结构。",
                related_observables=[],
                source=FieldSource.MODEL_RECOMMENDED,
                status=FieldStatus.AWAITING_CONFIRMATION,
                confidence=0.5,
            ))

        return goals

    @staticmethod
    def _has_observable(
        spec: CylinderFlow2DExperimentSpecV1,
        obs_type: ObservableType,
    ) -> bool:
        """Return ``True`` if *spec* contains an observable of *obs_type*."""
        return any(obs.type == obs_type for obs in spec.observables)


__all__ = ["CylinderFlow2DAnalysisGoalBuilder"]
