"""Parameter Ontology — structured knowledge base of physical parameters.

Defines parameter categories, relationships, units, valid ranges, and
physical meaning.  This is the foundation for the Dynamic Schema Engine.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)


class ParameterCategory(str, Enum):
    """Physical category of a parameter."""

    GEOMETRY = "geometry"
    BOUNDARY_CONDITION = "boundary_condition"
    MATERIAL_PROPERTY = "material_property"
    FLOW_REGIME = "flow_regime"
    NUMERICAL = "numerical"
    TURBULENCE = "turbulence"
    DIMENSIONLESS = "dimensionless"
    MESH = "mesh"
    SOLVER = "solver"
    TIME = "time"


class RelationType(str, Enum):
    """Type of relationship between parameters."""

    DEPENDS_ON = "depends_on"
    DERIVES_FROM = "derives_from"
    CONSTRAINTS = "constraints"
    INCOMPATIBLE_WITH = "incompatible_with"
    REQUIRES = "requires"


class OntologyEntry(StrictModel):
    """A single parameter entry in the ontology.

    Attributes:
        parameter_id: Unique identifier (e.g., "reynolds_number").
        display_name: Human-readable name.
        category: Physical category.
        unit: SI unit (e.g., "m", "m/s", "dimensionless").
        si_unit: Canonical SI unit for normalization.
        unit_aliases: Alternative unit names that map to si_unit.
        data_type: "float", "integer", or "enum".
        typical_range: (min, max) tuple of typical physical range.
        physical_meaning: Description of what this parameter represents.
        formula: How this parameter is computed (for derived params).
        relation_targets: Parameters this one relates to.
        code_bindings: OpenFOAM dict files this parameter affects.
    """

    parameter_id: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=200)
    category: ParameterCategory
    unit: str = Field(default="dimensionless", max_length=50)
    si_unit: str = Field(default="dimensionless", max_length=50)
    unit_aliases: tuple[str, ...] = Field(default_factory=tuple, max_length=20)
    data_type: str = Field(default="float", max_length=20)
    typical_range_min: float | None = None
    typical_range_max: float | None = None
    physical_meaning: str = Field(default="", max_length=2000)
    formula: str = Field(default="", max_length=1000)
    relation_type: RelationType | None = None
    relation_targets: tuple[str, ...] = Field(default_factory=tuple, max_length=50)
    code_bindings: tuple[str, ...] = Field(default_factory=tuple, max_length=20)

    @model_validator(mode="after")
    def validate_range(self) -> OntologyEntry:
        if (
            self.typical_range_min is not None
            and self.typical_range_max is not None
            and self.typical_range_min >= self.typical_range_max
        ):
            raise ValueError("typical_range_min must be less than typical_range_max")
        return self


class ParameterOntology:
    """Registry of physical parameters with lookup and traversal.

    The ontology is a flat registry with explicit relationship links.
    It supports:
    - Lookup by ID or category
    - Dependency graph traversal
    - Unit normalization
    - Code binding resolution
    """

    def __init__(self, entries: tuple[OntologyEntry, ...] = ()) -> None:
        self._entries: dict[str, OntologyEntry] = {}
        for entry in entries:
            self.register(entry)

    def register(self, entry: OntologyEntry) -> None:
        if entry.parameter_id in self._entries:
            raise ValueError(
                f"parameter '{entry.parameter_id}' already registered"
            )
        self._entries[entry.parameter_id] = entry

    def get(self, parameter_id: str) -> OntologyEntry | None:
        return self._entries.get(parameter_id)

    def by_category(self, category: ParameterCategory) -> list[OntologyEntry]:
        return [
            e for e in self._entries.values() if e.category == category
        ]

    def all_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._entries.keys()))

    def dependencies_of(self, parameter_id: str) -> list[str]:
        """Return parameters that this parameter depends on or derives from."""
        entry = self._entries.get(parameter_id)
        if entry is None:
            return []
        if entry.relation_type in (RelationType.DEPENDS_ON, RelationType.DERIVES_FROM):
            return list(entry.relation_targets)
        return []

    def dependents_of(self, parameter_id: str) -> list[str]:
        """Return parameters that depend on this parameter."""
        result = []
        for eid, entry in self._entries.items():
            if (
                entry.relation_type in (RelationType.DEPENDS_ON, RelationType.DERIVES_FROM)
                and parameter_id in entry.relation_targets
            ):
                result.append(eid)
        return result

    def code_bindings_for(self, parameter_ids: list[str]) -> list[str]:
        """Collect all unique OpenFOAM dict files affected by these parameters."""
        files: list[str] = []
        seen: set[str] = set()
        for pid in parameter_ids:
            entry = self._entries.get(pid)
            if entry is None:
                continue
            for binding in entry.code_bindings:
                if binding not in seen:
                    seen.add(binding)
                    files.append(binding)
        return files

    def normalize_unit(self, parameter_id: str, value: float, from_unit: str) -> float:
        """Normalize a value from a given unit to the SI unit.

        Only supports a small set of common fluid mechanics conversions.
        """
        entry = self._entries.get(parameter_id)
        if entry is None:
            raise KeyError(f"unknown parameter: {parameter_id}")

        if from_unit == entry.si_unit or from_unit == entry.unit:
            return value

        # Common conversions
        conversions: dict[str, float] = {
            # Length
            "mm": 0.001, "cm": 0.01, "m": 1.0, "km": 1000.0,
            "inch": 0.0254, "ft": 0.3048,
            # Velocity
            "m/s": 1.0, "cm/s": 0.01, "mm/s": 0.001,
            "km/h": 1.0 / 3.6, "mph": 0.44704,
            # Pressure
            "Pa": 1.0, "kPa": 1000.0, "MPa": 1e6, "bar": 1e5,
            "atm": 101325.0, "psi": 6894.76,
            # Density
            "kg/m3": 1.0, "g/cm3": 1000.0,
            # Viscosity
            "m2/s": 1.0, "cSt": 1e-6, "mm2/s": 1e-6,
            # Temperature
            "K": 1.0, "C": 1.0,  # offset handled separately
            # Time
            "s": 1.0, "ms": 0.001, "min": 60.0, "h": 3600.0,
        }

        factor = conversions.get(from_unit)
        if factor is None:
            raise ValueError(
                f"cannot convert '{from_unit}' to '{entry.si_unit}'"
            )
        return value * factor

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, parameter_id: str) -> bool:
        return parameter_id in self._entries


def _geometry_entries() -> list[OntologyEntry]:
    return [
        OntologyEntry(
            parameter_id="diameter",
            display_name="直径",
            category=ParameterCategory.GEOMETRY,
            unit="m", si_unit="m",
            unit_aliases=("mm", "cm", "inch"),
            typical_range_min=0.001,
            typical_range_max=10.0,
            physical_meaning="Characteristic diameter of the geometry",
            code_bindings=("system/blockMeshDict",),
        ),
        OntologyEntry(
            parameter_id="length",
            display_name="长度",
            category=ParameterCategory.GEOMETRY,
            unit="m", si_unit="m",
            unit_aliases=("mm", "cm"),
            typical_range_min=0.01,
            typical_range_max=100.0,
            physical_meaning="Length of the flow domain",
            code_bindings=("system/blockMeshDict",),
        ),
        OntologyEntry(
            parameter_id="side_length",
            display_name="边长",
            category=ParameterCategory.GEOMETRY,
            unit="m", si_unit="m",
            typical_range_min=0.01,
            typical_range_max=10.0,
            physical_meaning="Side length of square cavity",
            code_bindings=("system/blockMeshDict",),
        ),
        OntologyEntry(
            parameter_id="domain_width",
            display_name="计算域宽度",
            category=ParameterCategory.GEOMETRY,
            unit="D", si_unit="D",
            typical_range_min=5.0,
            typical_range_max=50.0,
            physical_meaning="Domain width in cylinder diameters (upstream)",
            code_bindings=("system/blockMeshDict",),
        ),
        OntologyEntry(
            parameter_id="domain_height",
            display_name="计算域高度",
            category=ParameterCategory.GEOMETRY,
            unit="D", si_unit="D",
            typical_range_min=5.0,
            typical_range_max=50.0,
            physical_meaning="Domain height in cylinder diameters (downstream)",
            code_bindings=("system/blockMeshDict",),
        ),
    ]


def _boundary_condition_entries() -> list[OntologyEntry]:
    return [
        OntologyEntry(
            parameter_id="inlet_velocity",
            display_name="入口速度",
            category=ParameterCategory.BOUNDARY_CONDITION,
            unit="m/s", si_unit="m/s",
            unit_aliases=("cm/s", "mm/s"),
            typical_range_min=0.001,
            typical_range_max=100.0,
            physical_meaning="Uniform inlet velocity magnitude",
            code_bindings=("0/U",),
        ),
        OntologyEntry(
            parameter_id="mean_velocity",
            display_name="平均速度",
            category=ParameterCategory.BOUNDARY_CONDITION,
            unit="m/s", si_unit="m/s",
            typical_range_min=0.001,
            typical_range_max=50.0,
            physical_meaning="Cross-section averaged mean velocity",
            code_bindings=("0/U",),
        ),
        OntologyEntry(
            parameter_id="lid_velocity",
            display_name="盖板速度",
            category=ParameterCategory.BOUNDARY_CONDITION,
            unit="m/s", si_unit="m/s",
            typical_range_min=0.01,
            typical_range_max=10.0,
            physical_meaning="Velocity of the moving lid in cavity",
            code_bindings=("0/U",),
        ),
        OntologyEntry(
            parameter_id="mass_flow_rate",
            display_name="质量流量",
            category=ParameterCategory.BOUNDARY_CONDITION,
            unit="kg/s", si_unit="kg/s",
            typical_range_min=0.001,
            typical_range_max=1000.0,
            physical_meaning="Mass flow rate at inlet",
            code_bindings=("0/U",),
        ),
        OntologyEntry(
            parameter_id="outlet_pressure",
            display_name="出口压力",
            category=ParameterCategory.BOUNDARY_CONDITION,
            unit="Pa", si_unit="Pa",
            typical_range_min=0.0,
            typical_range_max=1e7,
            physical_meaning="Pressure at outlet boundary",
            code_bindings=("0/p",),
        ),
    ]


def _material_entries() -> list[OntologyEntry]:
    return [
        OntologyEntry(
            parameter_id="density",
            display_name="密度",
            category=ParameterCategory.MATERIAL_PROPERTY,
            unit="kg/m3", si_unit="kg/m3",
            unit_aliases=("g/cm3",),
            typical_range_min=0.1,
            typical_range_max=20000.0,
            physical_meaning="Fluid density",
            code_bindings=("constant/physicalProperties",),
        ),
        OntologyEntry(
            parameter_id="kinematic_viscosity",
            display_name="运动粘度",
            category=ParameterCategory.MATERIAL_PROPERTY,
            unit="m2/s", si_unit="m2/s",
            unit_aliases=("cSt", "mm2/s"),
            typical_range_min=1e-9,
            typical_range_max=1e-2,
            physical_meaning="Kinematic viscosity of the fluid",
            code_bindings=("constant/physicalProperties",),
        ),
    ]


def _dimensionless_entries() -> list[OntologyEntry]:
    return [
        OntologyEntry(
            parameter_id="reynolds_number",
            display_name="Reynolds 数",
            category=ParameterCategory.DIMENSIONLESS,
            unit="dimensionless", si_unit="dimensionless",
            typical_range_min=0.1,
            typical_range_max=1e8,
            physical_meaning="Ratio of inertial to viscous forces",
            formula="rho * U * D / mu",
            relation_type=RelationType.DERIVES_FROM,
            relation_targets=("diameter", "inlet_velocity", "density", "kinematic_viscosity"),
            code_bindings=(),
        ),
        OntologyEntry(
            parameter_id="strouhal_number",
            display_name="Strouhal 数",
            category=ParameterCategory.DIMENSIONLESS,
            unit="dimensionless", si_unit="dimensionless",
            typical_range_min=0.1,
            typical_range_max=0.3,
            physical_meaning="Dimensionless vortex shedding frequency",
            formula="f * D / U",
            relation_type=RelationType.DERIVES_FROM,
            relation_targets=("diameter", "inlet_velocity"),
        ),
    ]


def _numerical_entries() -> list[OntologyEntry]:
    return [
        OntologyEntry(
            parameter_id="end_time",
            display_name="结束时间",
            category=ParameterCategory.TIME,
            unit="s", si_unit="s",
            unit_aliases=("ms", "min"),
            typical_range_min=0.1,
            typical_range_max=1e6,
            physical_meaning="Simulation end time",
            code_bindings=("system/controlDict",),
        ),
        OntologyEntry(
            parameter_id="time_step",
            display_name="时间步长",
            category=ParameterCategory.TIME,
            unit="s", si_unit="s",
            unit_aliases=("ms",),
            typical_range_min=1e-8,
            typical_range_max=1.0,
            physical_meaning="Time step for transient simulation",
            code_bindings=("system/controlDict",),
        ),
        OntologyEntry(
            parameter_id="max_courant",
            display_name="最大 Courant 数",
            category=ParameterCategory.NUMERICAL,
            unit="dimensionless", si_unit="dimensionless",
            typical_range_min=0.01,
            typical_range_max=10.0,
            physical_meaning="Maximum allowed Courant number",
            relation_type=RelationType.DEPENDS_ON,
            relation_targets=("time_step", "inlet_velocity"),
            code_bindings=("system/controlDict",),
        ),
        OntologyEntry(
            parameter_id="cells_radial",
            display_name="径向网格数",
            category=ParameterCategory.MESH,
            unit="dimensionless", si_unit="dimensionless",
            data_type="integer",
            typical_range_min=10,
            typical_range_max=500,
            physical_meaning="Number of cells in radial direction",
            code_bindings=("system/blockMeshDict",),
        ),
        OntologyEntry(
            parameter_id="cells_wake",
            display_name="尾流网格数",
            category=ParameterCategory.MESH,
            unit="dimensionless", si_unit="dimensionless",
            data_type="integer",
            typical_range_min=20,
            typical_range_max=1000,
            physical_meaning="Number of cells in wake region",
            code_bindings=("system/blockMeshDict",),
        ),
        OntologyEntry(
            parameter_id="axial_cells",
            display_name="轴向网格数",
            category=ParameterCategory.MESH,
            unit="dimensionless", si_unit="dimensionless",
            data_type="integer",
            typical_range_min=10,
            typical_range_max=1000,
            physical_meaning="Number of cells in axial direction",
            code_bindings=("system/blockMeshDict",),
        ),
        OntologyEntry(
            parameter_id="radial_cells",
            display_name="径向网格数",
            category=ParameterCategory.MESH,
            unit="dimensionless", si_unit="dimensionless",
            data_type="integer",
            typical_range_min=5,
            typical_range_max=200,
            physical_meaning="Number of cells in radial direction",
            code_bindings=("system/blockMeshDict",),
        ),
        OntologyEntry(
            parameter_id="cells_per_side",
            display_name="每边网格数",
            category=ParameterCategory.MESH,
            unit="dimensionless", si_unit="dimensionless",
            data_type="integer",
            typical_range_min=10,
            typical_range_max=500,
            physical_meaning="Number of cells per side of cavity",
            code_bindings=("system/blockMeshDict",),
        ),
    ]


def default_ontology() -> ParameterOntology:
    """Create a ParameterOntology pre-populated with standard fluid mechanics parameters."""
    entries: list[OntologyEntry] = []
    entries.extend(_geometry_entries())
    entries.extend(_boundary_condition_entries())
    entries.extend(_material_entries())
    entries.extend(_dimensionless_entries())
    entries.extend(_numerical_entries())
    return ParameterOntology(tuple(entries))


__all__ = [
    "OntologyEntry",
    "ParameterCategory",
    "ParameterOntology",
    "RelationType",
    "default_ontology",
]
