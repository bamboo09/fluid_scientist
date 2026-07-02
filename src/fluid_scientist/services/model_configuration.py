"""Immutable, atomically replaceable model-provider application state."""

from dataclasses import dataclass
from typing import Literal, Protocol

from fluid_scientist.adapters.openai_provider import ExperimentDesign
from fluid_scientist.experiment_planning import ExperimentDesigner

ProviderName = Literal["openai", "glm", "deepseek"]


class LegacyExperimentDesigner(Protocol):
    def design_experiment(
        self, question: str, *, capabilities: tuple[str, ...]
    ) -> ExperimentDesign: ...


@dataclass(frozen=True, slots=True)
class ModelConfiguration:
    """One coherent snapshot for legacy and provider-neutral planning."""

    provider: ProviderName | None = None
    model: str | None = None
    plan_designer: ExperimentDesigner | None = None
    legacy_designer: LegacyExperimentDesigner | None = None

    def __post_init__(self) -> None:
        neutral_fields = (self.provider, self.model, self.plan_designer)
        if any(value is not None for value in neutral_fields) and not all(
            value is not None for value in neutral_fields
        ):
            raise ValueError(
                "provider, model, and plan_designer must be configured together"
            )

    @property
    def configured(self) -> bool:
        return self.plan_designer is not None
