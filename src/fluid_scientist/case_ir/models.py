"""RequestedCaseIR and ResolvedCaseIR data models.

This module defines the Pydantic v2 data structures for the Case IR
(Intermediate Representation) layer introduced in Phase 2 of the
Fluid Scientist refactor.

The :class:`RequestedCaseIR` captures the full scientific intent of a
simulation case -- entities, regions, relations, physics, boundary
conditions, observables, and all associated metadata -- without committing
to any specific OpenFOAM implementation.  The :class:`ResolvedCaseIR`
is produced after capability resolution and serves as the sole input to
the deterministic OpenFOAM 13 compiler.

All models enforce ``extra="forbid"`` via :class:`_CaseIRBase` to catch
schema drift early.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Base model -- shared config
# ---------------------------------------------------------------------------


class _CaseIRBase(BaseModel):
    """Base class for all Case IR models.

    Enforces ``extra="forbid"`` so that unexpected fields raise a
    :class:`~pydantic.ValidationError` rather than being silently
    accepted.
    """

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# 1. ParameterValue -- unified parameter structure
# ---------------------------------------------------------------------------


class ParameterValue(_CaseIRBase):
    """A single parameter value with provenance metadata.

    Every physical or numerical parameter in the Case IR is wrapped in a
    :class:`ParameterValue` so that the system always knows *where* the
    value came from, *how confident* it is, and *what alternatives* exist.

    Attributes:
        value: The parameter value (number, string, list, or dict).
        unit: Physical unit string; ``"dimensionless"`` by default.
        source: How the value was obtained (user, model, system, etc.).
        confidence: Confidence score in the range ``[0.0, 1.0]``.
        status: Resolution status of the parameter.
        assumption: Free-text description of any assumption made.
        derived_from: References to other parameter paths this value was
            derived from.
        alternatives: Alternative candidate values with metadata.
    """

    value: Any
    unit: str = "dimensionless"
    source: Literal[
        "USER_EXPLICIT",
        "USER_CONFIRMED",
        "MODEL_INFERRED",
        "MODEL_RECOMMENDED",
        "SYSTEM_DEFAULT",
        "FORMULA_DERIVED",
        "CAPABILITY_REQUIRED",
        "TEMPLATE_DERIVED",
        "LITERATURE_SUGGESTED",
    ]
    confidence: float = 1.0
    status: Literal[
        "CONFIRMED",
        "INFERRED",
        "RECOMMENDED",
        "ASSUMED",
        "UNRESOLVED",
        "AMBIGUOUS",
        "CONFLICTING",
    ] = "CONFIRMED"
    assumption: str | None = None
    derived_from: list[str] = Field(default_factory=list)
    alternatives: list[dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 2. Entity -- geometric object
# ---------------------------------------------------------------------------


class Entity(_CaseIRBase):
    """A geometric object in the simulation domain.

    Attributes:
        id: Unique identifier for the entity.
        kind: Geometric primitive or source type.
        parameters: Entity-specific dimensions and properties, each
            wrapped in a :class:`ParameterValue`.
        motion: Reference to a motion intent (if the entity moves).
    """

    id: str
    kind: Literal[
        "cylinder",
        "sphere",
        "box",
        "pipe",
        "plane_wall",
        "nozzle",
        "imported_stl",
        "custom",
    ]
    parameters: dict[str, ParameterValue] = Field(default_factory=dict)
    motion: str | None = None


# ---------------------------------------------------------------------------
# 3. Region -- solution domain
# ---------------------------------------------------------------------------


class Region(_CaseIRBase):
    """A solution domain region.

    Attributes:
        id: Unique identifier for the region.
        kind: Physical nature of the region.
        material_ref: Reference to a :class:`Material` id.
        physics_refs: References to physics intents active in this region.
    """

    id: str
    kind: Literal["fluid", "solid", "porous"]
    material_ref: str = ""
    physics_refs: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 4. Relation -- spatial relationship between entities
# ---------------------------------------------------------------------------


class Relation(_CaseIRBase):
    """A spatial relationship between two entities.

    Attributes:
        id: Unique identifier for the relation.
        type: Spatial relationship type.
        source: Source entity id.
        target: Target entity id.
        parameters: Relation-specific parameters (gap, angle, etc.).
    """

    id: str
    type: Literal[
        "near",
        "inside",
        "intersects",
        "aligned_with",
        "inclined_to",
        "upstream_of",
        "downstream_of",
        "attached_to",
        "rotates_about",
        "moves_along",
    ]
    source: str
    target: str
    parameters: dict[str, ParameterValue] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# 5. Interface -- coupling between regions
# ---------------------------------------------------------------------------


class Interface(_CaseIRBase):
    """A coupling interface between two regions.

    Attributes:
        id: Unique identifier for the interface.
        region_a: First region id.
        region_b: Second region id.
        coupling_intent: Type of physical coupling at the interface.
    """

    id: str
    region_a: str
    region_b: str
    coupling_intent: Literal[
        "conjugate_heat_transfer",
        "fluid_structure_interaction",
        "porous_interface",
        "none",
    ]


# ---------------------------------------------------------------------------
# 6. Material
# ---------------------------------------------------------------------------


class Material(_CaseIRBase):
    """A material definition with physical properties.

    Attributes:
        id: Unique identifier for the material.
        kind: Material classification.
        properties: Physical properties, each wrapped in a
            :class:`ParameterValue`.
    """

    id: str
    kind: Literal[
        "newtonian_fluid",
        "non_newtonian_fluid",
        "solid",
        "porous",
    ]
    properties: dict[str, ParameterValue] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# 7. FieldSpec
# ---------------------------------------------------------------------------


class FieldSpec(_CaseIRBase):
    """Specification for a simulation field variable.

    Attributes:
        name: Field name (U, p, k, omega, etc.).
        field_class: OpenFOAM field class.
        dimensions: Dimension vector string, e.g. ``"[0 1 -1 0 0 0 0]"``.
        internal_field: Internal field specification, e.g.
            ``"uniform (0 0 0)"``.
    """

    name: str
    field_class: Literal[
        "volVectorField",
        "volScalarField",
        "surfaceScalarField",
    ]
    dimensions: str
    internal_field: str


# ---------------------------------------------------------------------------
# 8. BoundaryIntent
# ---------------------------------------------------------------------------


class BoundaryIntent(_CaseIRBase):
    """A boundary condition intent at the semantic level.

    Rather than directly specifying an OpenFOAM boundary condition type,
    a :class:`BoundaryIntent` captures *what* the boundary should do
    semantically (e.g. ``"uniform_velocity_inlet"``,
    ``"no_slip_wall"``).  The capability resolver later maps this to a
    concrete, verified boundary condition component.

    Attributes:
        id: Unique identifier for the boundary intent.
        target_patch: Name of the target mesh patch.
        semantic_role: Semantic description of the boundary behaviour.
        capability_ref: Reference to a resolved capability id, if any.
        parameters: Boundary-specific parameters.
        fields: Field names this boundary condition applies to.
    """

    id: str
    target_patch: str
    semantic_role: str
    capability_ref: str | None = None
    parameters: dict[str, ParameterValue] = Field(default_factory=dict)
    fields: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 9. InitialConditionIntent
# ---------------------------------------------------------------------------


class InitialConditionIntent(_CaseIRBase):
    """An initial condition intent at the semantic level.

    Attributes:
        id: Unique identifier for the initial condition intent.
        target: Target region or field reference.
        semantic_role: Semantic description of the initial state (e.g.
            ``"quiescent"``, ``"uniform"``, ``"developed"``).
        parameters: Initial-condition-specific parameters.
    """

    id: str
    target: str
    semantic_role: str
    parameters: dict[str, ParameterValue] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# 10. OperatingStage
# ---------------------------------------------------------------------------


class OperatingStage(_CaseIRBase):
    """A stage in a multi-stage operating scenario.

    Complex cases may involve initialization, ramp-up, flow development,
    condition switching, and measurement phases.  Each stage is
    represented by an :class:`OperatingStage`.

    Attributes:
        id: Unique identifier for the stage.
        type: Stage type.
        time_range: Optional ``[start, end]`` time window.
        actions: List of action identifiers to perform during this stage.
        observable_refs: Observables that should be sampled during this
            stage.
    """

    id: str
    type: Literal[
        "initialization",
        "transient_ramp",
        "flow_development",
        "measurement",
        "工况切换",
    ]
    time_range: list[float] | None = None
    actions: list[str] = Field(default_factory=list)
    observable_refs: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 11. Observable
# ---------------------------------------------------------------------------


class Observable(_CaseIRBase):
    """A scientific observable / measurement target.

    An :class:`Observable` describes *what* the user wants to measure,
    *where* and *when* to sample, *which fields* are needed, and *how*
    to analyse the sampled data.  The capability resolver determines
    whether the required OpenFOAM sampling capability and external
    analysis capability are available.

    Attributes:
        id: Unique identifier for the observable.
        semantic_type: Scientific type (e.g. ``"drag_coefficient"``,
            ``"wake_flip"``, ``"frequency_spectrum"``).
        target_region: Region where the observable is measured.
        required_fields: Field names required for this observable.
        sampling: Sampling configuration (type, frequency, stage ref).
        analysis: Analysis configuration (method, parameters).
        capability_status: Current capability resolution status.
        capability_ref: Reference to a resolved capability id, if any.
        openfoam_sampling_capability: OpenFOAM function object type
            (e.g. ``"forces"``, ``"probes"``).
        external_analysis_capability: External analysis method (e.g.
            ``"FFT"``, ``"wake_flip_detection"``).
    """

    id: str
    semantic_type: str
    target_region: str = ""
    required_fields: list[str] = Field(default_factory=list)
    sampling: dict[str, Any] = Field(default_factory=dict)
    analysis: dict[str, Any] = Field(default_factory=dict)
    capability_status: Literal[
        "UNRESOLVED",
        "SUPPORTED",
        "COMPOSABLE",
        "EXTENDABLE",
        "REQUIRES_NEW_PHYSICS",
    ] = "UNRESOLVED"
    capability_ref: str | None = None
    openfoam_sampling_capability: str | None = None
    external_analysis_capability: str | None = None


# ---------------------------------------------------------------------------
# 12. DerivedConstraint
# ---------------------------------------------------------------------------


class DerivedConstraint(_CaseIRBase):
    """A derived parameter constraint expressed as a formula.

    Attributes:
        id: Unique identifier for the constraint.
        expression: Formula expression, e.g.
            ``"Re = U_ref * L_ref / nu"``.
        inputs: Parameter paths that serve as inputs to the formula.
        output: Parameter path where the result is stored.
    """

    id: str
    expression: str
    inputs: list[str] = Field(default_factory=list)
    output: str


# ---------------------------------------------------------------------------
# 13. Assumption
# ---------------------------------------------------------------------------


class Assumption(_CaseIRBase):
    """An assumption made during case construction.

    Attributes:
        id: Unique identifier for the assumption.
        description: Human-readable description of the assumption.
        impact: Estimated impact on simulation results.
        reversible: Whether the assumption can be undone later.
    """

    id: str
    description: str
    impact: Literal["low", "medium", "high"] = "medium"
    reversible: bool = True


# ---------------------------------------------------------------------------
# 14. Ambiguity
# ---------------------------------------------------------------------------


class Ambiguity(_CaseIRBase):
    """An ambiguity detected during parsing.

    When a user's description can be interpreted in multiple ways, the
    ambiguity is preserved with all candidate concepts so it can be
    resolved later through clarification.

    Attributes:
        id: Unique identifier for the ambiguity.
        raw_text: The original ambiguous text from the user.
        candidate_concepts: List of candidate interpretations with
            confidence scores.
        status: Current resolution status.
        resolution: How the ambiguity was resolved, if applicable.
    """

    id: str
    raw_text: str
    candidate_concepts: list[dict[str, Any]] = Field(default_factory=list)
    status: Literal["AMBIGUOUS", "RESOLVED", "BLOCKING"] = "AMBIGUOUS"
    resolution: str | None = None


# ---------------------------------------------------------------------------
# 15. UnresolvedRequirement
# ---------------------------------------------------------------------------


class UnresolvedRequirement(_CaseIRBase):
    """A requirement that could not be resolved during decomposition.

    Attributes:
        id: Unique identifier for the requirement.
        description: Human-readable description of what is needed.
        blocking: Whether this requirement blocks case generation.
        reason: Why the requirement could not be resolved.
    """

    id: str
    description: str
    blocking: bool = True
    reason: str = ""


# ---------------------------------------------------------------------------
# 16. ExtensionSpecRef
# ---------------------------------------------------------------------------


class ExtensionSpecRef(_CaseIRBase):
    """A reference to an extension specification.

    When a required capability is missing, an extension specification is
    created.  This reference links the Case IR to that extension.

    Attributes:
        id: Unique identifier for the extension reference.
        extension_type: Type of extension required.
        description: What the extension should accomplish.
        target_capability: Capability id that the extension will satisfy.
        parameters: Extension-specific parameters.
    """

    id: str
    extension_type: Literal["config", "code", "physics"]
    description: str
    target_capability: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# 17. PhysicsIntent
# ---------------------------------------------------------------------------


class PhysicsIntent(_CaseIRBase):
    """High-level physics configuration for the case.

    Attributes:
        flow_regime: Compressibility regime.
        time_mode: Steady or transient.
        turbulence: Turbulence modelling approach.
        turbulence_model: Specific turbulence model name (e.g.
            ``"kOmegaSST"``, ``"LESWALE"``).
        heat_transfer: Whether heat transfer is active.
        multiphase: Whether multiphase flow is active.
        porous_media: Whether porous media modelling is active.
        moving_mesh: Whether mesh motion is active.
        additional_physics: Additional physics flags or identifiers.
    """

    flow_regime: Literal["incompressible", "compressible"] = "incompressible"
    time_mode: Literal["steady", "transient"] = "transient"
    turbulence: Literal["laminar", "RANS", "LES", "DES", "DNS"] = "laminar"
    turbulence_model: str = ""
    heat_transfer: bool = False
    multiphase: bool = False
    porous_media: bool = False
    moving_mesh: bool = False
    additional_physics: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 18. MeshIntent
# ---------------------------------------------------------------------------


class MeshIntent(_CaseIRBase):
    """Mesh generation intent.

    Attributes:
        strategy: Mesh generation strategy.
        refinement_zones: Zones requiring mesh refinement.
        boundary_layer: Boundary layer configuration.
        target_y_plus: Target y+ value for near-wall resolution.
        cell_count_estimate: Estimated total cell count.
    """

    strategy: Literal["block_mesh", "snappy_hex_mesh", "imported"] = "block_mesh"
    refinement_zones: list[dict[str, Any]] = Field(default_factory=list)
    boundary_layer: dict[str, Any] = Field(default_factory=dict)
    target_y_plus: ParameterValue | None = None
    cell_count_estimate: int | None = None


# ---------------------------------------------------------------------------
# 19. NumericalIntent
# ---------------------------------------------------------------------------


class NumericalIntent(_CaseIRBase):
    """Numerical scheme and solver configuration intent.

    Attributes:
        max_courant_number: Maximum Courant number constraint.
        pressure_velocity_coupling: Pressure-velocity coupling algorithm.
        schemes: Discretisation scheme mappings (e.g.
            ``{"divScheme": "linearUpwind"}``).
        tolerances: Solver tolerance settings.
    """

    max_courant_number: ParameterValue | None = None
    pressure_velocity_coupling: Literal["SIMPLE", "PIMPLE", "PISO"] = "PIMPLE"
    schemes: dict[str, str] = Field(default_factory=dict)
    tolerances: dict[str, ParameterValue] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# 20. RequestedCaseIR -- the main model
# ---------------------------------------------------------------------------


class RequestedCaseIR(_CaseIRBase):
    """The main Case Intermediate Representation.

    A :class:`RequestedCaseIR` captures the complete scientific intent of
    a simulation case as understood from the user's natural-language
    description.  It is the central artefact of the decomposition stage
    and the input to capability resolution.

    The IR is versioned so that modifications within a session create new
    versions rather than overwriting the previous state.

    Attributes:
        schema_version: Schema version string.
        case_ir_version: Monotonically increasing version number.
        study_id: Parent study identifier.
        case_id: Unique case identifier.
        physics: High-level physics configuration.
        entities: Geometric objects in the domain.
        regions: Solution domain regions.
        relations: Spatial relationships between entities.
        interfaces: Coupling interfaces between regions.
        materials: Material definitions.
        fields: Field variable specifications.
        boundary_intents: Semantic boundary condition intents.
        initial_conditions: Semantic initial condition intents.
        operating_stages: Multi-stage operating scenario.
        mesh_intent: Mesh generation intent.
        numerical_intent: Numerical scheme and solver intent.
        observables: Scientific measurement targets.
        derived_constraints: Formula-based parameter constraints.
        assumptions: Assumptions made during construction.
        ambiguities: Ambiguities detected during parsing.
        unresolved_requirements: Requirements that could not be resolved.
        extensions: References to extension specifications.
    """

    schema_version: str = "2.0"
    case_ir_version: int = 1
    study_id: str
    case_id: str
    physics: PhysicsIntent = Field(default_factory=PhysicsIntent)
    entities: list[Entity] = Field(default_factory=list)
    regions: list[Region] = Field(default_factory=list)
    relations: list[Relation] = Field(default_factory=list)
    interfaces: list[Interface] = Field(default_factory=list)
    materials: list[Material] = Field(default_factory=list)
    fields: list[FieldSpec] = Field(default_factory=list)
    boundary_intents: list[BoundaryIntent] = Field(default_factory=list)
    initial_conditions: list[InitialConditionIntent] = Field(default_factory=list)
    operating_stages: list[OperatingStage] = Field(default_factory=list)
    mesh_intent: MeshIntent = Field(default_factory=MeshIntent)
    numerical_intent: NumericalIntent = Field(default_factory=NumericalIntent)
    observables: list[Observable] = Field(default_factory=list)
    derived_constraints: list[DerivedConstraint] = Field(default_factory=list)
    assumptions: list[Assumption] = Field(default_factory=list)
    ambiguities: list[Ambiguity] = Field(default_factory=list)
    unresolved_requirements: list[UnresolvedRequirement] = Field(
        default_factory=list
    )
    extensions: list[ExtensionSpecRef] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 21. ResolvedCapability
# ---------------------------------------------------------------------------


class ResolvedCapability(_CaseIRBase):
    """A resolved capability mapping from requirement to implementation.

    Attributes:
        requirement_id: The atomic requirement identifier.
        capability_id: The resolved capability identifier.
        validation_status: Whether the capability has been verified.
    """

    requirement_id: str
    capability_id: str
    validation_status: Literal["VERIFIED", "UNVERIFIED"] = "VERIFIED"


# ---------------------------------------------------------------------------
# 22. CompositionPlan
# ---------------------------------------------------------------------------


class CompositionPlan(_CaseIRBase):
    """A plan for composing OpenFOAM case components.

    Attributes:
        base_pack: Base pack identifier (e.g.
            ``"foundation13-incompressible-les-transient"``).
        geometry_components: Geometry component identifiers.
        boundary_components: Boundary condition component identifiers.
        mesh_components: Mesh component identifiers.
        observable_components: Observable component identifiers.
        validation_components: Validation component identifiers.
    """

    base_pack: str = ""
    geometry_components: list[str] = Field(default_factory=list)
    boundary_components: list[str] = Field(default_factory=list)
    mesh_components: list[str] = Field(default_factory=list)
    observable_components: list[str] = Field(default_factory=list)
    validation_components: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 23. ResolvedCaseIR
# ---------------------------------------------------------------------------


class ResolvedCaseIR(_CaseIRBase):
    """The resolved Case IR produced after capability resolution.

    A :class:`ResolvedCaseIR` is the sole input to the deterministic
    OpenFOAM 13 compiler.  It captures the runtime configuration, resolved
    physics, resolved capabilities, and the composition plan.

    Attributes:
        requested_case_ir_version: Version of the source
            :class:`RequestedCaseIR`.
        runtime: Runtime configuration (platform profile, application,
            solver module).
        resolved_physics: Resolved physics configuration.
        resolved_capabilities: List of resolved capability mappings.
        composition_plan: Component composition plan.
    """

    requested_case_ir_version: int
    runtime: dict[str, str] = Field(default_factory=dict)
    resolved_physics: dict[str, Any] = Field(default_factory=dict)
    resolved_capabilities: list[ResolvedCapability] = Field(
        default_factory=list
    )
    composition_plan: CompositionPlan = Field(default_factory=CompositionPlan)


__all__ = [
    "Ambiguity",
    "Assumption",
    "BoundaryIntent",
    "CompositionPlan",
    "DerivedConstraint",
    "Entity",
    "ExtensionSpecRef",
    "FieldSpec",
    "InitialConditionIntent",
    "Interface",
    "Material",
    "MeshIntent",
    "NumericalIntent",
    "Observable",
    "OperatingStage",
    "ParameterValue",
    "PhysicsIntent",
    "Region",
    "Relation",
    "RequestedCaseIR",
    "ResolvedCaseIR",
    "ResolvedCapability",
    "UnresolvedRequirement",
]
