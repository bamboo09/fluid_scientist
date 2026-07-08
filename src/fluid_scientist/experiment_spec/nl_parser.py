"""Rule-based natural language parser for parameter modifications.

Parses Chinese instructions like:
  "把管径改成50毫米，长度改成5米，质量流量设为2kg/s"
into structured parameter change proposals.

This is a deterministic parser that works without LLM access.
It matches parameter display names and common Chinese aliases
against the ExperimentSpec's parameter list, then extracts
numeric values with unit conversion.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from fluid_scientist.experiment_spec.models import ExperimentSpec


@dataclass(frozen=True)
class ProposedChange:
    """A single proposed parameter change from NL parsing."""
    parameter_id: str
    display_name: str
    old_value: float | int | str | None
    new_value: float | int | str
    unit: str | None = None
    matched_term: str = ""


@dataclass(frozen=True)
class NLParseResult:
    """Result of parsing a natural language instruction."""
    proposed_changes: list[ProposedChange] = field(default_factory=list)
    unmatched_segments: list[str] = field(default_factory=list)
    requires_confirmation: bool = True


# Common Chinese aliases for parameter names
PARAMETER_ALIASES: dict[str, list[str]] = {
    "diameter": ["管径", "直径", "圆柱直径", "diameter", "D"],
    "length": ["管长", "长度", "length", "L"],
    "side_length": ["边长", "方腔边长", "side_length"],
    "inlet_velocity": ["入口速度", "进口速度", "来流速度", "inlet_velocity", "U"],
    "lid_velocity": ["顶盖速度", "盖板速度", "lid_velocity"],
    "mean_velocity": ["平均速度", "mean_velocity"],
    "density": ["密度", "density", "rho", "ρ"],
    "kinematic_viscosity": ["运动粘度", "粘度", "运动黏度", "黏度", "viscosity", "nu", "ν"],
    "reynolds_number": ["雷诺数", "reynolds", "Re", "雷诺"],
    "mass_flow_rate": ["质量流量", "mass_flow_rate", "流量"],
    "end_time": ["结束时间", "仿真时间", "end_time", "总时间"],
    "time_step": ["时间步长", "步长", "time_step", "deltaT", "dt"],
    "max_courant": ["最大courant", "courant数", "库朗数", "max_courant", "Co"],
    "domain_width": ["域宽", "计算域宽", "上游长度", "domain_width"],
    "domain_height": ["域高", "计算域高", "下游长度", "domain_height"],
    "cells_radial": ["径向网格", "径向网格数", "cells_radial"],
    "cells_wake": ["尾流网格", "尾流网格数", "cells_wake"],
    "axial_cells": ["轴向网格", "轴向网格数", "axial_cells"],
    "radial_cells": ["径向网格", "径向网格数", "radial_cells"],
    "cells_per_side": ["每边网格", "每边网格数", "cells_per_side"],
}

# Unit conversion factors to base units
UNIT_CONVERSIONS: dict[str, tuple[str, float]] = {
    # Length
    "m": ("m", 1.0),
    "米": ("m", 1.0),
    "mm": ("m", 0.001),
    "毫米": ("m", 0.001),
    "cm": ("m", 0.01),
    "厘米": ("m", 0.01),
    "dm": ("m", 0.1),
    "分米": ("m", 0.1),
    "km": ("m", 1000.0),
    "千米": ("m", 1000.0),
    # Velocity
    "m/s": ("m/s", 1.0),
    "m s-1": ("m/s", 1.0),
    # Density
    "kg/m3": ("kg/m3", 1.0),
    "kg/m^3": ("kg/m3", 1.0),
    "kg m-3": ("kg/m3", 1.0),
    # Viscosity
    "m2/s": ("m^2/s", 1.0),
    "m^2/s": ("m^2/s", 1.0),
    "m2 s-1": ("m^2/s", 1.0),
    # Flow rate
    "kg/s": ("kg/s", 1.0),
    # Dimensionless
    "D": ("D", 1.0),
    # Time
    "s": ("s", 1.0),
    "秒": ("s", 1.0),
    "ms": ("s", 0.001),
    "毫秒": ("s", 0.001),
}


def _extract_numbers_with_units(text: str) -> list[tuple[float, str]]:
    """Extract (value, unit) pairs from text.

    Handles patterns like:
      50毫米, 0.05m, 2kg/s, 1e-6, 100
    """
    results: list[tuple[float, str]] = []

    # Pattern: number followed by optional unit
    # Supports: integers, decimals, scientific notation
    # Units: ASCII unit tokens or common Chinese unit words
    # Chinese units are ordered longest-first so e.g. "毫米" wins over "米".
    pattern = re.compile(
        r'(\d+\.?\d*(?:[eE][+-]?\d+)?)'  # number
        r'\s*'  # optional space
        r'([a-zA-Z²³¹/\^\-\.]+|毫米|厘米|分米|千米|毫秒|秒|米)?'  # unit
    )

    for match in pattern.finditer(text):
        try:
            value = float(match.group(1))
        except ValueError:
            continue
        unit = match.group(2) or ""
        results.append((value, unit))

    return results


def _convert_value(
    value: float, unit: str, target_unit: str | None
) -> tuple[float, str | None]:
    """Convert a value from one unit to target unit."""
    if not unit or not target_unit:
        return value, target_unit

    # If unit matches target, no conversion needed
    if unit == target_unit:
        return value, target_unit

    # Check conversion table
    if unit in UNIT_CONVERSIONS:
        converted_unit, factor = UNIT_CONVERSIONS[unit]
        if converted_unit == target_unit:
            return value * factor, target_unit

    # No conversion possible, return as-is
    return value, unit if unit else target_unit


def _find_parameter_by_alias(
    term: str, spec: ExperimentSpec
) -> str | None:
    """Find a parameter ID by matching against display names and aliases."""
    term_lower = term.lower().strip()

    # First try exact match on display_name
    for p in spec.parameters:
        if p.display_name.lower() == term_lower:
            return p.parameter_id
        if p.parameter_id.lower() == term_lower:
            return p.parameter_id

    # Try alias matching
    for param_id, aliases in PARAMETER_ALIASES.items():
        for alias in aliases:
            if alias.lower() == term_lower and any(
                p.parameter_id == param_id for p in spec.parameters
            ):
                    return param_id

    # Try partial matching (term contains alias or alias contains term)
    for param_id, aliases in PARAMETER_ALIASES.items():
        for alias in aliases:
            if (alias in term or term in alias) and any(
                p.parameter_id == param_id for p in spec.parameters
            ):
                    return param_id

    return None


def parse_nl_instruction(
    instruction: str, spec: ExperimentSpec
) -> NLParseResult:
    """Parse a natural language instruction into proposed parameter changes.

    Args:
        instruction: Natural language text like "把管径改成50毫米，长度改成5米"
        spec: The current experiment spec to match parameters against

    Returns:
        NLParseResult with proposed changes and unmatched segments
    """
    proposed_changes: list[ProposedChange] = []
    unmatched: list[str] = []

    # Split by common delimiters
    segments = re.split(r'[,，;；\n]+', instruction)

    for segment in segments:
        segment = segment.strip()
        if not segment:
            continue

        # Try to find a parameter name in this segment
        matched_param_id = None
        matched_term = ""

        # Try all aliases, longest first for better matching
        all_aliases: list[tuple[str, str]] = []
        for param_id, aliases in PARAMETER_ALIASES.items():
            for alias in aliases:
                all_aliases.append((alias, param_id))
        all_aliases.sort(key=lambda x: len(x[0]), reverse=True)

        for alias, param_id in all_aliases:
            if alias in segment and any(
                p.parameter_id == param_id for p in spec.parameters
            ):
                    matched_param_id = param_id
                    matched_term = alias
                    break

        # Also try display names from the spec
        if not matched_param_id:
            for p in spec.parameters:
                if p.display_name in segment:
                    matched_param_id = p.parameter_id
                    matched_term = p.display_name
                    break

        if not matched_param_id:
            unmatched.append(segment)
            continue

        # Extract numeric value from the segment
        numbers = _extract_numbers_with_units(segment)
        if not numbers:
            unmatched.append(segment)
            continue

        # Take the first number as the new value
        raw_value, raw_unit = numbers[0]

        # Find the parameter to get its target unit
        param = spec.get_parameter(matched_param_id)
        if param is None:
            unmatched.append(segment)
            continue

        # Convert value to target unit if possible
        converted_value, final_unit = _convert_value(raw_value, raw_unit, param.unit)

        # Convert to int if the parameter data_type is integer
        if param.data_type == "integer":
            converted_value = int(converted_value)

        proposed_changes.append(ProposedChange(
            parameter_id=matched_param_id,
            display_name=param.display_name,
            old_value=param.value,
            new_value=converted_value,
            unit=final_unit or param.unit,
            matched_term=matched_term,
        ))

    return NLParseResult(
        proposed_changes=proposed_changes,
        unmatched_segments=unmatched,
        requires_confirmation=len(proposed_changes) > 0,
    )
