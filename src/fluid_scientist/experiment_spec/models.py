"""ExperimentSpec and ParameterSpec unified data structures.

This module implements the structured experiment specification described in
the dynamic experiment system reform specification.  All experiment data
uses these structured models as the single source of truth — no fixed
text blobs.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    StringConstraints,
    model_validator,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)


# --- Enums ---

class ExperimentStatus(str, Enum):
    DRAFT = "draft"
    READY = "ready"
    CONFIRMED = "confirmed"
    COMPILING = "compiling"
    AWAITING_CODE_APPROVAL = "awaiting_code_approval"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"


class TaskType(str, Enum):
    NEW_SIMULATION = "new_simulation"
    PAPER_REPRODUCTION = "paper_reproduction"
    BENCHMARK_REPRODUCTION = "benchmark_reproduction"
    PARAMETER_SENSITIVITY = "parameter_sensitivity"
    MECHANISM_ANALYSIS = "mechanism_analysis"
    ENGINEERING_PREDICTION = "engineering_prediction"
    GEOMETRY_OPTIMIZATION = "geometry_optimization"
    MODEL_COMPARISON = "model_comparison"
    CASE_DIAGNOSIS = "case_diagnosis"
    POST_PROCESSING = "post_processing"
    BATCH_DATA_GENERATION = "batch_data_generation"


class InteractionMode(str, Enum):
    QUICK = "quick"
    STANDARD = "standard"
    EXPERT = "expert"


class ParameterSource(str, Enum):
    USER = "user"
    DERIVED = "derived"
    SYSTEM_RECOMMENDED = "system_recommended"
    TEMPLATE_DEFAULT = "template_default"
    LITERATURE = "literature"
    GENERATED_BY_CODE = "generated_by_code"
    UNKNOWN = "unknown"


class Criticality(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ConfirmationPolicy(str, Enum):
    REQUIRE_EXPLICIT = "require_explicit"
    RECOMMEND_AND_NOTIFY = "recommend_and_notify"
    AUTO_ACCEPT = "auto_accept"
    HIDDEN_ADVANCED = "hidden_advanced"


class ParameterStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    MODIFIED = "modified"
    REJECTED = "rejected"


class Dimensions(str, Enum):
    TWO_D = "2D"
    THREE_D = "3D"
    AXISYMMETRIC = "axisymmetric"


class PhaseType(str, Enum):
    SINGLE_PHASE = "single_phase"
    MULTI_PHASE = "multi_phase"


class Compressibility(str, Enum):
    INCOMPRESSIBLE = "incompressible"
    COMPRESSIBLE = "compressible"
    LOW_MACH = "low_mach"


class FlowRegime(str, Enum):
    LAMINAR = "laminar"
    TURBULENT = "turbulent"
    TRANSITIONAL = "transitional"


class TemporalType(str, Enum):
    STEADY = "steady"
    TRANSIENT = "transient"


# --- Parameter Spec ---

class ParameterSourceInfo(StrictModel):
    type: ParameterSource
    reference: str | None = None
    reason: str | None = None
    applicability: str | None = None
    confidence: Literal["high", "medium", "low"] = "medium"
    risk_level: Literal["high", "medium", "low"] = "low"


class ParameterConstraints(StrictModel):
    min: float | int | None = None
    max: float | int | None = None
    exclusive_min: bool = False
    exclusive_max: bool = False
    allowed_values: list[str] | None = None


class ParameterDependency(StrictModel):
    depends_on: list[str] = Field(default_factory=list)
    affects: list[str] = Field(default_factory=list)


class ParameterProvenance(StrictModel):
    created_by: Literal["system", "user", "expert"] = "system"
    created_at: str | None = None
    last_modified_by: Literal["system", "user", "expert"] | None = None
    evidence: str | None = None


class CodeBinding(StrictModel):
    target_file: str
    target_path: str
    serializer: str = "scalar"


class ParameterSpec(StrictModel):
    """Structured parameter — never just a bare value."""

    parameter_id: Annotated[StrictStr, StringConstraints(min_length=1, max_length=128)]
    display_name: Annotated[StrictStr, StringConstraints(min_length=1, max_length=256)]
    category: Annotated[StrictStr, StringConstraints(min_length=1, max_length=64)]

    value: float | int | str | bool | None = None
    unit: str | None = None
    data_type: Literal["float", "integer", "string", "boolean", "enum"] = "float"

    source: ParameterSourceInfo
    status: ParameterStatus = ParameterStatus.PENDING
    editable: bool = True
    visible_level: InteractionMode = InteractionMode.STANDARD

    criticality: Criticality = Criticality.MEDIUM
    impact_scope: list[str] = Field(default_factory=list)
    confirmation_policy: ConfirmationPolicy = ConfirmationPolicy.RECOMMEND_AND_NOTIFY

    constraints: ParameterConstraints = Field(default_factory=ParameterConstraints)
    dependencies: ParameterDependency = Field(default_factory=ParameterDependency)
    validation_rules: list[str] = Field(default_factory=list)

    uncertainty: dict[str, Any] | None = None
    provenance: ParameterProvenance = Field(default_factory=ParameterProvenance)
    code_binding: CodeBinding | None = None

    @model_validator(mode="after")
    def validate_value_constraints(self) -> ParameterSpec:
        if self.value is None:
            if (
                self.criticality == Criticality.CRITICAL
                and self.source.type != ParameterSource.UNKNOWN
            ):
                raise ValueError(f"critical parameter {self.parameter_id} must have a value")
            return self
        if (
            self.constraints.allowed_values is not None
            and self.data_type == "enum"
            and str(self.value) not in self.constraints.allowed_values
        ):
            raise ValueError(f"parameter {self.parameter_id} value not in allowed_values")
        if self.data_type in ("float", "integer") and self.value is not None:
            v = float(self.value)
            if self.constraints.min is not None:
                if self.constraints.exclusive_min and v <= self.constraints.min:
                    raise ValueError(f"parameter {self.parameter_id} below exclusive min")
                if not self.constraints.exclusive_min and v < self.constraints.min:
                    raise ValueError(f"parameter {self.parameter_id} below min")
            if self.constraints.max is not None:
                if self.constraints.exclusive_max and v >= self.constraints.max:
                    raise ValueError(f"parameter {self.parameter_id} above exclusive max")
                if not self.constraints.exclusive_max and v > self.constraints.max:
                    raise ValueError(f"parameter {self.parameter_id} above max")
        return self


# --- Physics Spec ---

class PhysicsSpec(StrictModel):
    dimensions: Dimensions = Dimensions.TWO_D
    phases: PhaseType = PhaseType.SINGLE_PHASE
    compressibility: Compressibility = Compressibility.INCOMPRESSIBLE
    flow_regime: FlowRegime = FlowRegime.LAMINAR
    temporal_type: TemporalType = TemporalType.STEADY
    gravity_enabled: bool = False


# --- Research Spec ---

class ResearchSpec(StrictModel):
    title: Annotated[StrictStr, StringConstraints(min_length=1, max_length=512)]
    objective: Annotated[StrictStr, StringConstraints(min_length=1, max_length=4000)]
    hypothesis: str | None = None
    comparison_target: str | None = None
    user_questions: list[str] = Field(default_factory=list)


# --- Experiment Spec (top-level) ---

class ExperimentSpec(StrictModel):
    """Unified experiment specification — the single source of truth."""

    experiment_id: Annotated[StrictStr, StringConstraints(min_length=1, max_length=128)]
    schema_version: Annotated[StrictStr, StringConstraints(pattern=r"^\d+\.\d+\.\d+$")] = "1.0.0"
    experiment_version: int = Field(default=1, ge=1)

    status: ExperimentStatus = ExperimentStatus.DRAFT
    task_type: TaskType = TaskType.NEW_SIMULATION
    interaction_mode: InteractionMode = InteractionMode.STANDARD

    research: ResearchSpec
    physics: PhysicsSpec = Field(default_factory=PhysicsSpec)

    parameters: list[ParameterSpec] = Field(default_factory=list)
    metrics: list[dict[str, Any]] = Field(default_factory=list)
    sampling_plan: dict[str, Any] | None = None
    validation_plan: dict[str, Any] | None = None
    compute_plan: dict[str, Any] | None = None

    code_extensions: list[dict[str, Any]] = Field(default_factory=list)
    approval_records: list[dict[str, Any]] = Field(default_factory=list)
    change_history: list[dict[str, Any]] = Field(default_factory=list)

    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

    @model_validator(mode="after")
    def validate_parameters(self) -> ExperimentSpec:
        ids = [p.parameter_id for p in self.parameters]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate parameter_id in parameters")
        return self

    def get_parameter(self, parameter_id: str) -> ParameterSpec | None:
        for p in self.parameters:
            if p.parameter_id == parameter_id:
                return p
        return None

    def update_parameter(
        self, parameter_id: str, new_value: float | int | str | bool
    ) -> ExperimentSpec:
        params = []
        found = False
        for p in self.parameters:
            if p.parameter_id == parameter_id:
                params.append(p.model_copy(update={
                    "value": new_value,
                    "status": ParameterStatus.MODIFIED,
                    "provenance": p.provenance.model_copy(
                        update={"last_modified_by": "user"}
                    ),
                }))
                found = True
            else:
                params.append(p)
        if not found:
            raise KeyError(f"parameter {parameter_id} not found")
        return self.model_copy(update={
            "parameters": params,
            "updated_at": datetime.now(UTC).isoformat()
        })

    def critical_unresolved(self) -> list[ParameterSpec]:
        return [
            p for p in self.parameters
            if p.criticality == Criticality.CRITICAL
            and (p.value is None or p.source.type == ParameterSource.UNKNOWN)
        ]

    def is_ready(self) -> bool:
        return len(self.critical_unresolved()) == 0
