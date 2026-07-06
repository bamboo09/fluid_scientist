"""Dynamic Schema Engine — parameter ontology, schema generation, and physics composition.

Implements P2 requirements:
- Parameter ontology with categories, relationships, and code bindings
- Dynamic schema generation from physics specifications
- Physics module composition (solver, turbulence, BC, schemes)
- Solver capability checking
- Unknown scenario fallback
"""

from fluid_scientist.dynamic_schema.ontology import (
    OntologyEntry,
    ParameterCategory,
    ParameterOntology,
    RelationType,
    default_ontology,
)
from fluid_scientist.dynamic_schema.physics_composition import (
    PhysicsModuleComposition,
    SolverCapability,
    TurbulenceModelSpec,
    check_solver_capability,
    compose_physics_modules,
    get_solver_capability,
    get_turbulence_model,
    handle_unknown_scenario,
    list_solvers,
    list_turbulence_models,
    recommend_turbulence_model,
)
from fluid_scientist.dynamic_schema.schema_engine import (
    FlowPhase,
    SchemaGenerationResult,
    detect_experiment_type,
    generate_schema,
)

__all__ = [
    "FlowPhase",
    "OntologyEntry",
    "ParameterCategory",
    "ParameterOntology",
    "PhysicsModuleComposition",
    "RelationType",
    "SchemaGenerationResult",
    "SolverCapability",
    "TurbulenceModelSpec",
    "check_solver_capability",
    "compose_physics_modules",
    "default_ontology",
    "detect_experiment_type",
    "generate_schema",
    "get_solver_capability",
    "get_turbulence_model",
    "handle_unknown_scenario",
    "list_solvers",
    "list_turbulence_models",
    "recommend_turbulence_model",
]
