"""Sampling plan mapping — generate DOE matrices from ExperimentSpec parameters.

Takes an ExperimentSpec and generates parameter sweep variants using different
strategies: full factorial, one-at-a-time (OAT), and random sampling.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from fluid_scientist.experiment_spec.models import (
    ExperimentSpec,
    ParameterSpec,
)


class SamplingStrategy(str, Enum):
    """Strategy for generating the sampling matrix."""

    FULL_FACTORIAL = "full_factorial"
    OAT = "one_at_a_time"
    RANDOM = "random"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)


class SamplingConfig(StrictModel):
    """Configuration for sampling plan generation.

    Attributes:
        strategy: Sampling strategy to use.
        num_samples: Number of samples for random strategy (ignored for
            full_factorial and oat).
        levels: Number of levels per parameter for full_factorial and oat.
        seed: Random seed for reproducibility.
        parameter_ids: Optional list of parameter IDs to include as design
            variables. If None, all parameters with range constraints are
            included.
    """

    strategy: SamplingStrategy = SamplingStrategy.OAT
    num_samples: int = Field(default=10, ge=1, le=1000)
    levels: int = Field(default=3, ge=2, le=20)
    seed: int = Field(default=42, ge=0)
    parameter_ids: tuple[str, ...] | None = None


@dataclass(frozen=True)
class SamplePoint:
    """A single sample point in the DOE matrix."""

    sample_id: str
    values: dict[str, Any]


@dataclass(frozen=True)
class SamplingPlan:
    """Complete sampling plan with all sample points."""

    strategy: SamplingStrategy
    design_variables: tuple[str, ...]
    samples: tuple[SamplePoint, ...]

    @property
    def num_samples(self) -> int:
        return len(self.samples)


def _get_design_variables(
    spec: ExperimentSpec,
    config: SamplingConfig,
) -> list[ParameterSpec]:
    """Identify design variables from the spec based on config."""
    if config.parameter_ids is not None:
        selected = set(config.parameter_ids)
        return [p for p in spec.parameters if p.parameter_id in selected]

    # Auto-select: parameters with range constraints (min and max)
    result = []
    for p in spec.parameters:
        if p.constraints is not None:
            has_range = p.constraints.min is not None and p.constraints.max is not None
            has_enum = bool(p.constraints.allowed_values)
            if has_range or has_enum:
                result.append(p)
    return result


def _generate_levels(param: ParameterSpec, num_levels: int) -> list[Any]:
    """Generate evenly spaced levels for a parameter."""
    if param.constraints is None:
        return [param.value]

    if param.constraints.allowed_values:
        values = list(param.constraints.allowed_values)
        if len(values) <= num_levels:
            return values
        # Subsample evenly
        step = len(values) / num_levels
        return [values[int(i * step)] for i in range(num_levels)]

    if param.constraints.min is not None and param.constraints.max is not None:
        lo = float(param.constraints.min)
        hi = float(param.constraints.max)
        if param.data_type == "integer":
            lo_i, hi_i = int(lo), int(hi)
            if hi_i - lo_i + 1 <= num_levels:
                return list(range(lo_i, hi_i + 1))
            step = (hi_i - lo_i) / (num_levels - 1) if num_levels > 1 else 0
            return [int(lo_i + i * step) for i in range(num_levels)]
        step = (hi - lo) / (num_levels - 1) if num_levels > 1 else 0
        return [lo + i * step for i in range(num_levels)]

    return [param.value]


def _full_factorial(
    design_vars: list[ParameterSpec],
    config: SamplingConfig,
) -> list[SamplePoint]:
    """Generate full factorial sampling matrix."""
    all_levels = [_generate_levels(p, config.levels) for p in design_vars]
    samples: list[SamplePoint] = []
    idx = 0

    def _recurse(dim: int, current: dict[str, Any]) -> None:
        nonlocal idx
        if dim == len(design_vars):
            samples.append(SamplePoint(f"s{idx:04d}", dict(current)))
            idx += 1
            return
        param = design_vars[dim]
        for value in all_levels[dim]:
            current[param.parameter_id] = value
            _recurse(dim + 1, current)

    _recurse(0, {})
    return samples


def _oat(
    spec: ExperimentSpec,
    design_vars: list[ParameterSpec],
    config: SamplingConfig,
) -> list[SamplePoint]:
    """Generate one-at-a-time sampling matrix."""
    baseline: dict[str, Any] = {
        p.parameter_id: p.value for p in spec.parameters
    }
    samples: list[SamplePoint] = [SamplePoint("s0000_baseline", dict(baseline))]
    idx = 1

    for param in design_vars:
        levels = _generate_levels(param, config.levels)
        for value in levels:
            if value == param.value:
                continue
            point = dict(baseline)
            point[param.parameter_id] = value
            samples.append(SamplePoint(f"s{idx:04d}_{param.parameter_id}", point))
            idx += 1

    return samples


def _random_sampling(
    design_vars: list[ParameterSpec],
    config: SamplingConfig,
) -> list[SamplePoint]:
    """Generate random sampling matrix."""
    rng = random.Random(config.seed)
    samples: list[SamplePoint] = []

    for i in range(config.num_samples):
        point: dict[str, Any] = {}
        for param in design_vars:
            if param.constraints is not None:
                if param.constraints.allowed_values:
                    point[param.parameter_id] = rng.choice(param.constraints.allowed_values)
                elif param.constraints.min is not None and param.constraints.max is not None:
                    lo = float(param.constraints.min)
                    hi = float(param.constraints.max)
                    if param.data_type == "integer":
                        point[param.parameter_id] = rng.randint(int(lo), int(hi))
                    else:
                        point[param.parameter_id] = rng.uniform(lo, hi)
                else:
                    point[param.parameter_id] = param.value
            else:
                point[param.parameter_id] = param.value
        samples.append(SamplePoint(f"s{i:04d}_random", point))

    return samples


def generate_sampling_plan(
    spec: ExperimentSpec,
    config: SamplingConfig | None = None,
) -> SamplingPlan:
    """Generate a sampling plan from an ExperimentSpec.

    Args:
        spec: The experiment specification with parameter constraints.
        config: Sampling configuration. If None, uses OAT with 3 levels.

    Returns:
        A SamplingPlan with all sample points.
    """
    if config is None:
        config = SamplingConfig()

    design_vars = _get_design_variables(spec, config)
    if not design_vars:
        return SamplingPlan(
            strategy=config.strategy,
            design_variables=(),
            samples=(SamplePoint("s0000_baseline", {}),),
        )

    if config.strategy == SamplingStrategy.FULL_FACTORIAL:
        samples = _full_factorial(design_vars, config)
    elif config.strategy == SamplingStrategy.OAT:
        samples = _oat(spec, design_vars, config)
    elif config.strategy == SamplingStrategy.RANDOM:
        samples = _random_sampling(design_vars, config)
    else:
        raise ValueError(f"unknown sampling strategy: {config.strategy}")

    return SamplingPlan(
        strategy=config.strategy,
        design_variables=tuple(p.parameter_id for p in design_vars),
        samples=tuple(samples),
    )


def create_spec_variant(
    spec: ExperimentSpec,
    sample: SamplePoint,
) -> ExperimentSpec:
    """Create an ExperimentSpec variant from a sample point.

    Updates the parameter values in the spec according to the sample point,
    keeping all other parameters unchanged.
    """
    updated_params = []
    for p in spec.parameters:
        if p.parameter_id in sample.values:
            new_p = p.model_copy(update={"value": sample.values[p.parameter_id]})
            updated_params.append(new_p)
        else:
            updated_params.append(p)
    return spec.model_copy(update={"parameters": tuple(updated_params)})


__all__ = [
    "SamplePoint",
    "SamplingConfig",
    "SamplingPlan",
    "SamplingStrategy",
    "create_spec_variant",
    "generate_sampling_plan",
]
