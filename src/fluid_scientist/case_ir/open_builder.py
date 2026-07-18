"""Build open, scenario-independent CaseIR from model understanding."""

from __future__ import annotations

from typing import Any

from fluid_scientist.llm.structured_understanding import StructuredUnderstanding

from .models import (
    BoundaryIntent,
    Entity,
    Observable,
    ParameterValue,
    PhysicsIntent,
    Region,
    Relation,
    RequestedCaseIR,
)


_NATIVE_ENTITY_KINDS = {
    "cylinder", "sphere", "box", "pipe", "plane_wall", "nozzle", "imported_stl"
}
_RELATION_TYPES = {
    "near", "inside", "intersects", "aligned_with", "inclined_to",
    "upstream_of", "downstream_of", "attached_to", "rotates_about", "moves_along",
}


def _parameter(value: Any, *, source: str = "MODEL_INFERRED", unit: str = "dimensionless") -> ParameterValue:
    return ParameterValue(
        value=value,
        unit=unit,
        source=source,
        status="CONFIRMED" if source in {"USER_EXPLICIT", "USER_CONFIRMED"} else "INFERRED",
        confidence=1.0 if source in {"USER_EXPLICIT", "USER_CONFIRMED"} else 0.8,
    )


class OpenCaseIRBuilder:
    """Convert semantic model output into one open CaseIR main chain.

    This builder performs schema mapping only.  It does not inspect the user's
    text, choose a case family, create semantic entities, or decide a solver.
    Those decisions must already be explicit in ``StructuredUnderstanding``.
    """

    def build(
        self,
        understanding: StructuredUnderstanding,
        *,
        study_id: str,
        case_id: str,
    ) -> RequestedCaseIR:
        entities: list[Entity] = []
        domain: dict[str, ParameterValue] = {}
        boundaries: list[BoundaryIntent] = []
        observables: list[Observable] = []
        physics_values: dict[str, Any] = {}

        fact_by_path = {fact.path: fact for fact in understanding.facts}
        for understood in understanding.entities:
            parameters: dict[str, ParameterValue] = {}
            for name, raw_value in understood.attributes.items():
                if isinstance(raw_value, dict) and "value" in raw_value:
                    parameters[name] = _parameter(
                        raw_value["value"],
                        unit=str(raw_value.get("unit", "dimensionless")),
                        source=str(raw_value.get("source", "MODEL_INFERRED")),
                    )
                else:
                    parameters[name] = _parameter(raw_value)
            native_kind = understood.semantic_type if understood.semantic_type in _NATIVE_ENTITY_KINDS else "custom"
            parameters.setdefault("semantic_type", _parameter(understood.semantic_type))
            entities.append(Entity(id=understood.entity_id, kind=native_kind, parameters=parameters))

        for path, fact in fact_by_path.items():
            name = path.rsplit("/", 1)[-1]
            source = fact.origin
            if path.startswith("/domain/"):
                domain[name] = _parameter(fact.value, source=source, unit=fact.unit or "dimensionless")
            elif path.startswith("/physics/"):
                physics_values[name] = fact.value
            elif path.startswith("/boundaries/"):
                boundary_id = path.split("/")[2]
                boundaries.append(BoundaryIntent(
                    id=f"bc_{boundary_id}",
                    target_patch=boundary_id,
                    semantic_role=str(fact.value),
                ))
            elif path.startswith("/observables/"):
                observable_id = path.split("/")[2]
                observables.append(Observable(
                    id=observable_id,
                    semantic_type=str(fact.value),
                ))

        relation_models = [
            Relation(
                id=f"rel_{index}",
                type=relation.predicate if relation.predicate in _RELATION_TYPES else "near",
                source=relation.subject_id,
                target=relation.object_id,
            )
            for index, relation in enumerate(understanding.relations, start=1)
        ]
        physics = PhysicsIntent(
            flow_regime=str(physics_values.get("flow_regime", "incompressible")),
            time_mode=str(physics_values.get("time_mode", "transient")),
            turbulence=str(physics_values.get("turbulence", "laminar")),
            heat_transfer=bool(physics_values.get("heat_transfer", False)),
            additional_physics=list(physics_values.get("additional_physics", [])),
        )
        return RequestedCaseIR(
            study_id=study_id,
            case_id=case_id,
            case_family=understanding.case_family,
            dimensionality=understanding.dimensionality,
            domain=domain,
            research_objectives=[understanding.summary],
            physics=physics,
            entities=entities,
            regions=[Region(id="fluid_region", kind="fluid")],
            relations=relation_models,
            boundary_intents=boundaries,
            observables=observables,
            ambiguities=[],
            unresolved_requirements=[],
        )
