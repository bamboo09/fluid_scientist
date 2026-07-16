from __future__ import annotations
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field

class ParameterValue(BaseModel):
    """A parameter value with provenance."""
    model_config = ConfigDict(extra="allow")
    value: float | str | None = None
    unit: str | None = None
    source_span: str | None = None
    confidence: float = 1.0

class Mention(BaseModel):
    """A single mention from user text."""
    model_config = ConfigDict(extra="allow")
    mention_id: str
    text: str
    category: Literal[
        "domain", "geometry", "material", "boundary",
        "initial_condition", "physics", "observable",
        "spatial_relation", "numerics", "unknown"
    ]
    status: Literal[
        "mapped", "derived", "ambiguous",
        "unsupported", "needs_clarification", "ignored"
    ] = "ignored"
    mapped_to: str | None = None

class MentionInventory(BaseModel):
    """Complete inventory of all user mentions."""
    model_config = ConfigDict(extra="allow")
    mentions: list[Mention] = Field(default_factory=list)
    
    @property
    def unaccounted_mentions(self) -> list[Mention]:
        return [m for m in self.mentions if m.status == "ignored"]
    
    @property
    def coverage_ratio(self) -> float:
        if not self.mentions:
            return 1.0
        accounted = len([m for m in self.mentions if m.status != "ignored"])
        return accounted / len(self.mentions)

class DomainIntent(BaseModel):
    model_config = ConfigDict(extra="allow")
    dimensionality: Literal["2D", "3D", "axisymmetric", "unknown"] = "unknown"
    length: ParameterValue | None = None
    width: ParameterValue | None = None  # width in 2D = height
    height: ParameterValue | None = None  # for 3D
    source_spans: list[str] = Field(default_factory=list)

class GeometryRepresentation(BaseModel):
    """Universal geometry representation."""
    model_config = ConfigDict(extra="allow")
    type: Literal[
        "circle", "ellipse", "parametric_polygon",
        "explicit_polygon", "profile_function", "csg",
        "imported_mesh", "implicit_surface", "unknown"
    ] = "unknown"
    subtype: str | None = None
    definition: dict[str, Any] = Field(default_factory=dict)

class GeometryEntity(BaseModel):
    """An open-world geometry entity."""
    model_config = ConfigDict(extra="allow")
    entity_id: str
    role: Literal[
        "domain", "immersed_obstacle", "wall_attached_obstacle",
        "solid_body", "porous_region", "inlet_geometry",
        "outlet_geometry", "unknown"
    ] = "unknown"
    raw_name: str = ""
    semantic_shape: str = "unknown"
    representation: GeometryRepresentation = Field(default_factory=GeometryRepresentation)
    parameters: dict[str, ParameterValue] = Field(default_factory=dict)
    relations: list[str] = Field(default_factory=list)
    source_spans: list[str] = Field(default_factory=list)
    confidence: float = 0.5
    representation_status: Literal[
        "resolved", "needs_clarification", "unsupported"
    ] = "needs_clarification"

class MaterialIntent(BaseModel):
    model_config = ConfigDict(extra="allow")
    material_id: str
    raw_name: str = ""
    phase: Literal["gas", "liquid", "solid", "multiphase", "unknown"] = "unknown"
    model: Literal[
        "incompressible_newtonian", "compressible_newtonian",
        "non_newtonian", "multiphase", "custom", "unknown"
    ] = "unknown"
    properties: dict[str, ParameterValue] = Field(default_factory=dict)
    source_spans: list[str] = Field(default_factory=list)
    missing_required_properties: list[str] = Field(default_factory=list)
    capability_status: str = "unknown"

class BoundaryIntent(BaseModel):
    model_config = ConfigDict(extra="allow")
    boundary_id: str
    target: str = ""  # e.g. "left", "right", "top", "bottom"
    physical_role: Literal[
        "velocity_inlet", "mass_flow_inlet", "pressure_inlet",
        "pressure_outlet", "open_boundary", "no_slip_wall",
        "slip_wall", "moving_wall", "symmetry", "periodic",
        "shear_stress", "heat_flux", "convective_outlet",
        "custom", "unknown"
    ] = "unknown"
    quantities: dict[str, ParameterValue] = Field(default_factory=dict)
    raw_text: str = ""
    source_span: str = ""
    semantic_status: str = "unknown"
    capability_status: str = "unknown"

class InitialConditionIntent(BaseModel):
    model_config = ConfigDict(extra="allow")
    ic_id: str
    field: str = ""
    value: ParameterValue | None = None
    region: str | None = None
    source_span: str = ""

class PhysicsModelIntent(BaseModel):
    model_config = ConfigDict(extra="allow")
    model_id: str
    raw_name: str = ""
    model_type: str = "unknown"  # e.g. "laminar", "turbulent_k_omega_sst"
    parameters: dict[str, ParameterValue] = Field(default_factory=dict)
    source_spans: list[str] = Field(default_factory=list)
    capability_status: str = "unknown"

class ObservableIntent(BaseModel):
    model_config = ConfigDict(extra="allow")
    observable_id: str
    raw_name: str = ""
    physical_quantity: str = ""
    target_entity: str | None = None
    spatial_scope: dict | None = None
    temporal_scope: dict | None = None
    statistic: str | None = None
    source_span: str = ""
    measurement_plan: str | None = None
    capability_status: str = "unknown"

class SpatialRelation(BaseModel):
    model_config = ConfigDict(extra="allow")
    relation_id: str
    subject_entity: str = ""
    relation_type: str = ""  # e.g. "centered_under", "attached_to", "distance_from"
    target_entity: str | None = None
    target_boundary: str | None = None
    parameters: dict[str, ParameterValue] = Field(default_factory=dict)
    source_span: str = ""

class SemanticAmbiguity(BaseModel):
    model_config = ConfigDict(extra="allow")
    ambiguity_id: str
    description: str = ""
    source_span: str = ""
    affected_entities: list[str] = Field(default_factory=list)
    blocking: bool = True

class Assumption(BaseModel):
    model_config = ConfigDict(extra="allow")
    assumption_id: str
    description: str = ""
    source: str = "system"  # "system" or "user"
    confidence: float = 0.5

class UnresolvedMention(BaseModel):
    model_config = ConfigDict(extra="allow")
    text: str = ""
    category: str = "unknown"
    reason: str = ""

class SourceCoverage(BaseModel):
    """Tracks coverage of user mentions."""
    model_config = ConfigDict(extra="allow")
    mention_inventory: MentionInventory = Field(default_factory=MentionInventory)
    
    @property
    def unaccounted_mentions(self) -> list[Mention]:
        return self.mention_inventory.unaccounted_mentions
    
    @property
    def coverage_ratio(self) -> float:
        return self.mention_inventory.coverage_ratio
    
    @property
    def is_complete(self) -> bool:
        return len(self.unaccounted_mentions) == 0

class OpenWorldResearchIR(BaseModel):
    """The canonical open-world research intermediate representation."""
    model_config = ConfigDict(extra="allow")
    
    ir_version: str = "1.0"
    study_type: str | None = None
    dimensionality: Literal["2D", "3D", "axisymmetric", "unknown"] = "unknown"
    
    domain: DomainIntent = Field(default_factory=DomainIntent)
    geometry_entities: list[GeometryEntity] = Field(default_factory=list)
    materials: list[MaterialIntent] = Field(default_factory=list)
    boundaries: list[BoundaryIntent] = Field(default_factory=list)
    initial_conditions: list[InitialConditionIntent] = Field(default_factory=list)
    physics_models: list[PhysicsModelIntent] = Field(default_factory=list)
    observables: list[ObservableIntent] = Field(default_factory=list)
    spatial_relations: list[SpatialRelation] = Field(default_factory=list)
    
    unresolved_mentions: list[UnresolvedMention] = Field(default_factory=list)
    ambiguities: list[SemanticAmbiguity] = Field(default_factory=list)
    assumptions: list[Assumption] = Field(default_factory=list)
    source_coverage: SourceCoverage = Field(default_factory=SourceCoverage)
    
    def get_entity(self, entity_id: str) -> GeometryEntity | None:
        for e in self.geometry_entities:
            if e.entity_id == entity_id:
                return e
        return None
    
    def get_entities_by_role(self, role: str) -> list[GeometryEntity]:
        return [e for e in self.geometry_entities if e.role == role]
