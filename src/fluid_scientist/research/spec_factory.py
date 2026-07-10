"""ExperimentSpec factory for the research-session draft workflow."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from fluid_scientist.compat import UTC
from fluid_scientist.experiment_spec.models import (
    Compressibility,
    ConfirmationPolicy,
    Criticality,
    Dimensions,
    ExperimentSpec,
    ExperimentStatus,
    FlowRegime,
    InteractionMode,
    ParameterConstraints,
    ParameterProvenance,
    ParameterSource,
    ParameterSourceInfo,
    ParameterSpec,
    ParameterStatus,
    PhaseType,
    PhysicsFieldMeta,
    PhysicsFieldStatus,
    PhysicsSpec,
    ResearchSpec,
    TaskType,
    TemporalType,
)
from fluid_scientist.measurement.boundary_verification_compiler import (
    BoundaryVerificationCompiler,
)
from fluid_scientist.measurement.goal_metric_compiler import GoalMetricCompiler
from fluid_scientist.research.models import (
    IntentAssessment,
    ResearchPhysicsSpec,
    ResearchSession,
)
from fluid_scientist.study_decomposition.models import ExtractedParameter, StudyIntent
from fluid_scientist.workbench.design_closure_engine import DesignClosureEngine
from fluid_scientist.workbench.experiment_design_synthesizer import (
    DesignField,
    ExperimentDesign,
    ExperimentDesignSynthesizer,
)


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (ValueError, TypeError):
        return None


class ExperimentSpecFactory:
    """Build a closed, editable draft instead of an empty parameter checklist."""

    def create_from_schema(
        self,
        session: ResearchSession,
        intent: IntentAssessment,
        physics_spec: ResearchPhysicsSpec | None,
    ) -> ExperimentSpec:
        """Create an ``ExperimentSpec`` for the legacy research-session route.

        The actual V5 page still calls ``/api/research-sessions``.  This method
        therefore runs the complete design chain here: full design synthesis,
        parameter closure, metric compilation, boundary verification, then
        capability checks outside this factory.
        """
        experiment_id = f"exp-{uuid4().hex[:16]}"
        now = datetime.now(UTC).isoformat()
        study = self._build_study_intent(session, intent, physics_spec)
        design = ExperimentDesignSynthesizer().synthesize(study)
        design = DesignClosureEngine().close(design)
        design = self._apply_explicit_physics(design, physics_spec)
        metric_groups = GoalMetricCompiler().compile(design)
        boundary_metrics = BoundaryVerificationCompiler().compile(design)

        design = design.model_copy(
            update={
                "scientific_metrics": metric_groups["scientific"],
                "boundary_verification_metrics": boundary_metrics,
                "credibility_metrics": metric_groups["credibility"],
            }
        )

        research = ResearchSpec(
            title=(intent.research_objective or session.original_request)[:512],
            objective=intent.research_objective or session.original_request,
            hypothesis=(
                design.research_hypotheses[0]
                if design.research_hypotheses
                else None
            ),
            comparison_target=intent.physical_system,
            user_questions=[session.original_request],
        )

        return ExperimentSpec(
            experiment_id=experiment_id,
            schema_version="1.0.0",
            experiment_version=1,
            status=ExperimentStatus.DRAFT,
            task_type=self._map_task_type(intent.task_type),
            interaction_mode=InteractionMode.STANDARD,
            research=research,
            physics=self._physics_from_design(design, physics_spec),
            parameters=self._parameters_from_design(design, session, now),
            metrics=self._metrics_from_design(design, metric_groups, boundary_metrics),
            sampling_plan={
                "sampling_strategy": design.sampling_strategy,
                "output_control": design.output_control,
            },
            validation_plan={
                "boundary_verification_metrics": boundary_metrics,
                "credibility_metrics": metric_groups["credibility"],
            },
            compute_plan=design.compute_resources,
            created_at=now,
            updated_at=now,
        )

    def _build_study_intent(
        self,
        session: ResearchSession,
        intent: IntentAssessment,
        physics_spec: ResearchPhysicsSpec | None,
    ) -> StudyIntent:
        full_text = session.accumulated_context.get("all_messages") or session.original_request
        known_parameters = [
            ExtractedParameter(
                canonical_id=key,
                display_name=key,
                value=value,
                unit=None,
                source_text=str(value),
                source="user_provided",
                confidence=0.9,
            )
            for key, value in self._extract_existing_params(session, physics_spec).items()
        ]
        boundary_conditions = self._boundary_conditions_from_physics(physics_spec)
        geometry = dict(physics_spec.geometry_facts) if physics_spec else {}
        if intent.physical_system and "type" not in geometry:
            geometry["type"] = self._geometry_type(intent.physical_system, full_text)

        analysis_goals = [
            *intent.target_phenomena,
            *intent.requested_metrics,
            *intent.explicitly_requested_metrics,
        ]
        if intent.research_objective:
            analysis_goals.append(intent.research_objective)

        return StudyIntent(
            study_id=f"study-{session.session_id}",
            title=(intent.research_objective or session.original_request)[:200],
            raw_text=full_text,
            study_type=geometry.get("type") or intent.physical_system or "unknown",
            research_objective=intent.research_objective or session.original_request,
            geometry=geometry,
            physical_models=self._physical_models_from_physics(physics_spec),
            initial_conditions=(
                [dict(physics_spec.initial_condition_facts)]
                if physics_spec and physics_spec.initial_condition_facts
                else []
            ),
            boundary_conditions=boundary_conditions,
            known_parameters=known_parameters,
            analysis_goals=analysis_goals,
            target_phenomena=list(intent.target_phenomena),
            boundary_facts=dict(physics_spec.boundary_facts) if physics_spec else {},
            readiness_level="draftable",
        )

    @staticmethod
    def _apply_explicit_physics(
        design: ExperimentDesign,
        physics_spec: ResearchPhysicsSpec | None,
    ) -> ExperimentDesign:
        if physics_spec is None or physics_spec.flow_regime != "turbulent":
            return design
        closed = design.model_copy(deep=True)
        closed.turbulence_model = {
            "model": "LES",
            "source": "USER_SPECIFIED",
            "reason": "User explicitly described the flow as turbulent.",
        }
        closed.dimensionless_parameters["target_y_plus"] = DesignField(
            value=1.0,
            source="SYSTEM_SELECTED",
            reason="Wall-resolved near-wall target selected for turbulent flow.",
            confidence=0.9,
        )
        return closed

    @staticmethod
    def _boundary_conditions_from_physics(
        physics_spec: ResearchPhysicsSpec | None,
    ) -> list[dict[str, Any]]:
        if physics_spec is None or not physics_spec.boundary_facts:
            return []
        result: list[dict[str, Any]] = []
        for key, value in physics_spec.boundary_facts.items():
            if isinstance(value, dict):
                patch = value.get("patch") or value.get("location") or key
                result.append({"patch": patch, "location": patch, **value})
            else:
                result.append({"patch": key, "location": key, "type": value})
        return result

    @staticmethod
    def _physical_models_from_physics(
        physics_spec: ResearchPhysicsSpec | None,
    ) -> dict[str, Any]:
        if physics_spec is None:
            return {}
        return {
            "dimensions": physics_spec.dimensions,
            "temporal_type": physics_spec.temporal_type,
            "phases": physics_spec.phases,
            "compressibility": physics_spec.compressibility,
            "flow_regime": physics_spec.flow_regime,
        }

    @staticmethod
    def _geometry_type(physical_system: str, text: str) -> str:
        lower = text.lower()
        if physical_system == "internal_flow":
            return "pipe"
        if physical_system == "external_flow":
            return "cylinder_external_flow" if ("圆柱" in text or "cylinder" in lower) else "external_flow"
        if physical_system == "cavity_flow":
            return "cavity"
        return physical_system

    @staticmethod
    def _physics_from_design(
        design: ExperimentDesign,
        research_physics: ResearchPhysicsSpec | None,
    ) -> PhysicsSpec:
        re_value = design.dimensionless_parameters.get("Re")
        flow_regime = (
            FlowRegime.TURBULENT
            if re_value and float(re_value.value) >= 3000
            else FlowRegime.LAMINAR
        )
        if research_physics and research_physics.flow_regime:
            flow_regime = (
                FlowRegime.TURBULENT
                if research_physics.flow_regime == "turbulent"
                else FlowRegime.LAMINAR
            )
        solver_name = str(design.solver.get("name", "pimpleFoam"))
        turbulence_model = str(design.turbulence_model.get("model", "laminar"))
        return PhysicsSpec(
            dimensions=Dimensions.THREE_D,
            phases=PhaseType.SINGLE_PHASE,
            compressibility=Compressibility.INCOMPRESSIBLE,
            flow_regime=flow_regime,
            temporal_type=TemporalType.TRANSIENT,
            gravity_enabled=False,
            solver=solver_name,
            turbulence_model=turbulence_model,
            field_status={
                "dimensions": PhysicsFieldMeta(
                    value=Dimensions.THREE_D.value,
                    status=PhysicsFieldStatus.DERIVED,
                    confidence="medium",
                    reason="Closed by the draft design generator.",
                    requires_confirmation=False,
                ),
                "solver": PhysicsFieldMeta(
                    value=solver_name,
                    status=PhysicsFieldStatus.DERIVED,
                    confidence="high",
                    reason=str(design.solver.get("reason", "")),
                    requires_confirmation=False,
                ),
                "turbulence_model": PhysicsFieldMeta(
                    value=turbulence_model,
                    status=PhysicsFieldStatus.DERIVED,
                    confidence="high",
                    reason=str(design.turbulence_model.get("reason", "")),
                    requires_confirmation=False,
                ),
            },
        )

    def _parameters_from_design(
        self,
        design: ExperimentDesign,
        session: ResearchSession,
        now: str,
    ) -> list[ParameterSpec]:
        params: list[ParameterSpec] = []
        for key, field in design.material_properties.items():
            params.append(self._field_param(key, key, "material", field, session, now))
        for key, field in design.dimensionless_parameters.items():
            params.append(self._field_param(key, key, "dimensionless", field, session, now))
        for key, field in design.parameterization_strategy.items():
            params.append(self._field_param(key, key, "reference", field, session, now))
        for key, value in design.computational_domain.items():
            params.append(self._plain_param(f"domain_{key}", key, "geometry", value, session, now))
        for patch, bc in design.boundary_conditions.items():
            params.append(
                self._plain_param(
                    f"bc_{patch}",
                    f"{patch} boundary",
                    "boundary_condition",
                    dict(bc),
                    session,
                    now,
                )
            )
        for field, initial in design.initial_conditions.items():
            params.append(
                self._plain_param(
                    f"initial_{field}",
                    f"initial {field}",
                    "initial_condition",
                    dict(initial),
                    session,
                    now,
                )
            )
        grouped = {
            "solver": design.solver,
            "turbulence_model": design.turbulence_model,
            "numerical_schemes": design.numerical_schemes,
            "pressure_velocity_coupling": design.pressure_velocity_coupling,
            "mesh_strategy": design.mesh_strategy,
            "near_wall_strategy": design.near_wall_strategy,
            "time_control": design.time_control,
            "sampling_strategy": design.sampling_strategy,
            "output_control": design.output_control,
            "post_processing": design.post_processing,
            "compute_resources": design.compute_resources,
        }
        for category, values in grouped.items():
            for key, value in values.items():
                if key in {"source", "reason"}:
                    continue
                params.append(
                    self._plain_param(
                        f"{category}_{key}",
                        key,
                        category,
                        value,
                        session,
                        now,
                        source=str(values.get("source", "SYSTEM_SELECTED")),
                        reason=str(values.get("reason", "")),
                    )
                )
        return self._dedupe(params)

    def _field_param(
        self,
        parameter_id: str,
        display_name: str,
        category: str,
        field: DesignField,
        session: ResearchSession,
        now: str,
    ) -> ParameterSpec:
        return self._param(
            parameter_id=parameter_id,
            display_name=display_name,
            category=category,
            value=field.value,
            unit=field.unit,
            source=field.source,
            reason=field.reason,
            confidence=field.confidence,
            editable=field.modifiable,
            session=session,
            now=now,
        )

    def _plain_param(
        self,
        parameter_id: str,
        display_name: str,
        category: str,
        value: Any,
        session: ResearchSession,
        now: str,
        source: str = "SYSTEM_SELECTED",
        reason: str = "",
    ) -> ParameterSpec:
        return self._param(
            parameter_id=parameter_id,
            display_name=display_name,
            category=category,
            value=value,
            unit=None,
            source=source,
            reason=reason,
            confidence=0.85,
            editable=True,
            session=session,
            now=now,
        )

    @staticmethod
    def _param(
        parameter_id: str,
        display_name: str,
        category: str,
        value: Any,
        unit: str | None,
        source: str,
        reason: str,
        confidence: float,
        editable: bool,
        session: ResearchSession,
        now: str,
    ) -> ParameterSpec:
        normalized_value, data_type = _normalize_value(value)
        mapped_source = _map_source(source)
        return ParameterSpec(
            parameter_id=_safe_id(parameter_id),
            display_name=display_name.replace("_", " "),
            category=category,
            value=normalized_value,
            unit=unit,
            data_type=data_type,
            source=ParameterSourceInfo(
                type=mapped_source,
                reason=reason or "Closed by the experiment design generator.",
                confidence="high" if confidence >= 0.85 else "medium",
                risk_level="low",
            ),
            status=ParameterStatus.ACCEPTED,
            editable=editable,
            visible_level=InteractionMode.STANDARD,
            criticality=Criticality.MEDIUM,
            confirmation_policy=ConfirmationPolicy.AUTO_ACCEPT,
            constraints=ParameterConstraints(),
            provenance=ParameterProvenance(
                created_by="system",
                created_at=now,
                source_type=mapped_source.value,
                research_session_id=session.session_id,
            ),
        )

    @staticmethod
    def _metrics_from_design(
        design: ExperimentDesign,
        metric_groups: dict[str, list[dict[str, Any]]],
        boundary_metrics: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return [
            {
                "kind": "analysis_goals",
                "analysis_goals": [goal.model_dump() for goal in design.analysis_goals],
                "target_phenomena": design.target_phenomena,
                "boundary_facts": design.boundary_facts,
            },
            {
                "kind": "compiled_metrics",
                "scientific_metrics": metric_groups["scientific"],
                "boundary_verification_metrics": boundary_metrics,
                "credibility_metrics": metric_groups["credibility"],
                "comparison_metrics": metric_groups["comparison"],
                "optional_diagnostics": metric_groups["optional_diagnostics"],
            },
        ]

    @staticmethod
    def _dedupe(parameters: list[ParameterSpec]) -> list[ParameterSpec]:
        seen: set[str] = set()
        result: list[ParameterSpec] = []
        for param in parameters:
            if param.parameter_id in seen:
                continue
            seen.add(param.parameter_id)
            result.append(param)
        return result

    @staticmethod
    def _extract_existing_params(
        session: ResearchSession,
        physics_spec: ResearchPhysicsSpec | None,
    ) -> dict[str, float]:
        params: dict[str, float] = {}
        for fact in session.confirmed_facts:
            key_lower = str(fact.key).lower()
            if key_lower in {"re", "reynolds", "reynolds_number"}:
                value = _to_float(fact.value)
                if value is not None:
                    params["Re"] = value
            elif key_lower in {"diameter", "pipe_diameter", "d"}:
                value = _to_float(fact.value)
                if value is not None:
                    params["D"] = value
        if physics_spec:
            for key, value in {
                **physics_spec.geometry_facts,
                **physics_spec.material_facts,
                **physics_spec.operating_conditions,
            }.items():
                numeric = _to_float(value)
                if numeric is not None:
                    params[key] = numeric
        return params

    @staticmethod
    def _map_task_type(task_type_str: str) -> TaskType:
        mapping = {
            "new_simulation": TaskType.NEW_SIMULATION,
            "parameter_sensitivity": TaskType.PARAMETER_SENSITIVITY,
            "mechanism_analysis": TaskType.MECHANISM_ANALYSIS,
            "engineering_prediction": TaskType.ENGINEERING_PREDICTION,
            "paper_reproduction": TaskType.PAPER_REPRODUCTION,
            "benchmark_reproduction": TaskType.BENCHMARK_REPRODUCTION,
            "model_comparison": TaskType.MODEL_COMPARISON,
            "case_diagnosis": TaskType.CASE_DIAGNOSIS,
        }
        return mapping.get(task_type_str, TaskType.NEW_SIMULATION)


def _safe_id(raw: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in raw)
    while "__" in safe:
        safe = safe.replace("__", "_")
    return safe.strip("_")[:128] or f"param_{uuid4().hex[:8]}"


def _normalize_value(value: Any) -> tuple[float | int | str | bool, str]:
    if isinstance(value, bool):
        return value, "boolean"
    if isinstance(value, int) and not isinstance(value, bool):
        return value, "integer"
    if isinstance(value, float):
        return value, "float"
    if isinstance(value, (dict, list, tuple)):
        return _compact(value), "string"
    return str(value), "string"


def _compact(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _map_source(source: str) -> ParameterSource:
    return {
        "USER_SPECIFIED": ParameterSource.USER,
        "SYSTEM_DERIVED": ParameterSource.DERIVED,
        "SYSTEM_SELECTED": ParameterSource.DERIVED,
        "TEMPLATE_DEFAULT": ParameterSource.TEMPLATE_DEFAULT,
        "ASSUMED_BASELINE": ParameterSource.TEMPLATE_DEFAULT,
    }.get(source, ParameterSource.DERIVED)


__all__ = ["ExperimentSpecFactory"]
