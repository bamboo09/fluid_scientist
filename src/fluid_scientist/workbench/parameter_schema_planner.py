"""Parameter schema planner — generates parameter lists from intent and physics.

Uses the existing schema_engine.generate_schema() and derivation.py
to produce pre-filled parameters with proper sources (user_confirmed,
derived, system_recommended, unknown_required).
"""

from __future__ import annotations

from typing import Any

from fluid_scientist.dynamic_schema.ontology import default_ontology
from fluid_scientist.dynamic_schema.schema_engine import (
    detect_experiment_type,
    generate_schema,
)
from fluid_scientist.experiment_spec.derivation import compute_derived_parameters
from fluid_scientist.experiment_spec.models import (
    Compressibility,
    ExperimentSpec,
    ExperimentStatus,
    FlowRegime,
    ParameterSource,
    ParameterSourceInfo,
    ParameterSpec,
    ParameterStatus,
    PhaseType,
    PhysicsSpec,
    ResearchSpec,
    TemporalType,
)

# Mapping from intent physical_system to experiment_type
_PHYSICAL_SYSTEM_MAP: dict[str, str] = {
    "pipe_flow": "laminar_pipe",
    "internal_flow": "laminar_pipe",
    "cylinder_external_flow": "cylinder_flow",
    "external_flow": "cylinder_flow",
    "cavity_flow": "lid_driven_cavity",
}

# Material database: common fluid properties
_MATERIAL_DB: dict[str, dict[str, float]] = {
    "water": {"density": 998.2, "kinematic_viscosity": 1e-6},
    "air": {"density": 1.225, "kinematic_viscosity": 1.5e-5},
}


class ParameterSchemaPlanner:
    """Plans parameter schema based on intent, physics, and metrics."""

    def plan(
        self,
        intent_assessment: dict,
        physics_spec: dict,
        metric_plan: list[dict],
        user_values: dict[str, Any],
    ) -> list[dict]:
        """Generate parameter list for the current problem.

        Uses existing schema_engine.generate_schema() and derivation.py
        to produce pre-filled parameters with proper sources.

        Args:
            intent_assessment: Intent assessment dict with fields like
                physical_system, geometry_type, etc.
            physics_spec: Physics specification dict with fields like
                compressibility, temporal_type, phases, flow_regime, etc.
            metric_plan: Metric plan list (currently unused but kept for
                future metric-driven parameter inference).
            user_values: User-provided parameter values keyed by
                parameter_id.

        Returns:
            List of parameter dicts with fields: parameter_id,
            display_name, category, unit, value, status, source,
            reason, criticality, editable, dependencies, affects.
        """
        # 1. Detect experiment type from intent
        experiment_type = self._detect_experiment_type(intent_assessment)

        # 2. Build PhysicsSpec from physics_spec dict
        physics = self._build_physics_spec(physics_spec)

        # 3. Merge user_values with material database recommendations
        merged_values = self._apply_material_db(user_values, intent_assessment)

        # 4. Call generate_schema with existing params
        ontology = default_ontology()
        schema_result = generate_schema(
            physics,
            ontology=ontology,
            existing_params=merged_values,
        )

        # 5. Build a temporary ExperimentSpec for derivation
        temp_spec = self._build_temp_spec(
            experiment_type, schema_result.parameters, merged_values
        )

        # 6. Apply derivation to fill derived values
        temp_spec = compute_derived_parameters(temp_spec)

        # 7. Convert parameters to dicts with proper status/source/reason
        return [
            self._param_to_dict(p, merged_values)
            for p in temp_spec.parameters
        ]

    def _detect_experiment_type(self, intent: dict) -> str:
        """Detect experiment type from intent assessment."""
        physical_system = intent.get("physical_system")
        if physical_system and physical_system in _PHYSICAL_SYSTEM_MAP:
            return _PHYSICAL_SYSTEM_MAP[physical_system]

        geometry_type = intent.get("geometry_type")
        if geometry_type:
            gt = geometry_type.lower()
            if "pipe" in gt or "tube" in gt:
                return "laminar_pipe"
            if "cylinder" in gt:
                return "cylinder_flow"
            if "cavity" in gt:
                return "lid_driven_cavity"

        # Fallback: use detect_experiment_type with empty params
        physics = self._build_physics_spec({})
        return detect_experiment_type(physics, {})

    @staticmethod
    def _build_physics_spec(physics_spec: dict) -> PhysicsSpec:
        """Build a PhysicsSpec from a dict."""
        kwargs: dict[str, Any] = {}

        compressibility = physics_spec.get("compressibility")
        if compressibility:
            kwargs["compressibility"] = Compressibility(compressibility)

        temporal_type = physics_spec.get("temporal_type")
        if temporal_type:
            kwargs["temporal_type"] = TemporalType(temporal_type)

        phases = physics_spec.get("phases")
        if phases:
            kwargs["phases"] = PhaseType(phases)

        flow_regime = physics_spec.get("flow_regime")
        if flow_regime:
            kwargs["flow_regime"] = FlowRegime(flow_regime)

        solver = physics_spec.get("solver")
        if solver:
            kwargs["solver"] = solver

        turbulence_model = physics_spec.get("turbulence_model")
        if turbulence_model:
            kwargs["turbulence_model"] = turbulence_model

        return PhysicsSpec(**kwargs)

    @staticmethod
    def _apply_material_db(
        user_values: dict[str, Any],
        intent: dict,
    ) -> dict[str, Any]:
        """Apply material database recommendations for known fluids."""
        merged = dict(user_values)

        # Try to detect fluid type from intent or user_values
        fluid_type = intent.get("fluid_type")
        if not fluid_type:
            fluid_type = merged.get("fluid")

        if fluid_type and isinstance(fluid_type, str):
            fluid_lower = fluid_type.lower()
            if fluid_lower in _MATERIAL_DB:
                props = _MATERIAL_DB[fluid_lower]
                # Only fill if not already user-provided
                if "density" not in merged:
                    merged["density"] = props["density"]
                if "kinematic_viscosity" not in merged:
                    merged["kinematic_viscosity"] = props[
                        "kinematic_viscosity"
                    ]

        return merged

    @staticmethod
    def _build_temp_spec(
        experiment_type: str,
        parameters: tuple[ParameterSpec, ...],
        user_values: dict[str, Any],
    ) -> ExperimentSpec:
        """Build a temporary ExperimentSpec for derivation."""
        # Mark user-provided parameters with USER source
        updated_params: list[ParameterSpec] = []
        for p in parameters:
            if p.parameter_id in user_values and user_values[p.parameter_id] is not None:
                updated_params.append(
                    p.model_copy(
                        update={
                            "value": user_values[p.parameter_id],
                            "source": ParameterSourceInfo(
                                type=ParameterSource.USER,
                                reason="User-provided value",
                            ),
                            "status": ParameterStatus.ACCEPTED,
                        }
                    )
                )
            else:
                updated_params.append(p)

        return ExperimentSpec(
            experiment_id="temp-for-derivation",
            research=ResearchSpec(
                title="Temporary spec for parameter derivation",
                objective="Derive parameter values",
            ),
            status=ExperimentStatus.DRAFT,
            parameters=updated_params,
        )

    @staticmethod
    def _param_to_dict(
        param: ParameterSpec,
        user_values: dict[str, Any],
    ) -> dict[str, Any]:
        """Convert a ParameterSpec to a dict with status/source/reason."""
        # Determine status string
        if param.parameter_id in user_values and param.source.type == ParameterSource.USER:
            status = "user_confirmed"
        elif param.source.type == ParameterSource.DERIVED:
            status = "derived"
        elif param.source.type == ParameterSource.UNKNOWN:
            status = "unknown_required"
        else:
            status = "system_recommended"

        return {
            "parameter_id": param.parameter_id,
            "display_name": param.display_name,
            "category": param.category,
            "unit": param.unit,
            "value": param.value,
            "status": status,
            "source": param.source.type.value,
            "reason": param.source.reason or "",
            "criticality": param.criticality.value,
            "editable": param.editable,
            "dependencies": param.dependencies.depends_on,
            "affects": param.dependencies.affects,
        }


__all__ = ["ParameterSchemaPlanner"]
