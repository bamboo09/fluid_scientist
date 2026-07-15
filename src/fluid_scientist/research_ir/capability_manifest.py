"""Real capability manifest for the Research IR.

This module defines the *real* capability manifest -- a structured Python
registry describing what the system **can actually do** natively.  It
replaces the old YAML skill files (``data/skills/fluid.*.yaml``) with a
single, typed, importable data structure.

The manifest is a *static registry*: it is populated at import time with
the :data:`DEFAULT_MANIFEST` instance and is intentionally free of
Pydantic so that it can be loaded without any model-validation overhead.
Consumers (the capability resolver, the gap analyzer, the semantic critic,
the capability planner and the experiment compiler) query it to answer
questions such as:

* "Is ``physics.laminar`` natively supported?"
* "Which capabilities are missing for this requested set?"
* "What OpenFOAM dictionary entries does ``boundary.velocity_inlet``
  emit?"
* "Which compiler hook handles ``geometry.circle``?"

Design notes
------------
* :class:`Capability` and :class:`CapabilityManifest` are plain
  ``dataclass`` objects (not Pydantic models) because the manifest is a
  read-only, hand-curated registry.
* The ``compiler_hook`` field carries the *name* of the compiler
  function (e.g. ``"compile_circle"``) that handles the capability.  The
  actual implementation lives in
  :mod:`fluid_scientist.research_ir.geometry_compiler` (for geometry) and
  sibling compiler modules.
* The ``openfoam_config`` field carries a dict of OpenFOAM dictionary
  fragments that the compiler emits for the capability.  Keys and values
  mirror the OpenFOAM 13 dictionary conventions used elsewhere in the
  codebase.
* A small backward-compatible layer (``has``, ``get``, ``list_by_category``,
  ``all_ids``, ``__contains__``, ``__len__``, ``register``,
  ``register_many``) is preserved so that existing consumers such as
  :class:`~fluid_scientist.research_ir.capability_planner.CapabilityPlanner`
  keep working unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Capability
# ---------------------------------------------------------------------------


@dataclass
class Capability:
    """A single registered capability.

    Attributes
    ----------
    capability_id:
        Dotted identifier, e.g. ``"geometry.circle"``, ``"physics.laminar"``.
    category:
        Broad grouping -- one of ``"geometry"``, ``"material"``,
        ``"physics"``, ``"boundary"``, ``"observable"``,
        ``"postprocessing"``.
    description:
        Human-readable description of what the capability does.
    supported:
        ``True`` if the capability is natively supported by the system.
        ``False`` marks a capability that is *known* but not yet
        implemented (useful for gap reporting).
    required_properties:
        The parameter names that must be supplied for the capability to
        be usable (e.g. ``["density", "viscosity"]`` for an
        incompressible Newtonian material).
    compiler_hook:
        Name of the compiler function that handles this capability, or
        ``None`` when no dedicated compiler exists.  For geometry
        capabilities these map to methods on
        :class:`~fluid_scientist.research_ir.geometry_compiler.PolygonGeometryCompiler`
        (e.g. ``"compile_circle"``).
    openfoam_config:
        OpenFOAM dictionary entries emitted by the compiler for this
        capability.  The exact keys depend on the category (see the
        pre-registered capabilities below for examples).
    """

    capability_id: str
    category: str
    description: str
    supported: bool
    required_properties: list[str] = field(default_factory=list)
    compiler_hook: str | None = None
    openfoam_config: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialize this capability to a plain ``dict``."""
        return {
            "capability_id": self.capability_id,
            "category": self.category,
            "description": self.description,
            "supported": self.supported,
            "required_properties": list(self.required_properties),
            "compiler_hook": self.compiler_hook,
            "openfoam_config": dict(self.openfoam_config),
        }


# ---------------------------------------------------------------------------
# CapabilityManifest
# ---------------------------------------------------------------------------


@dataclass
class CapabilityManifest:
    """A collection of registered :class:`Capability` objects.

    The manifest is the single source of truth for what the system can
    natively do.  It supports look-up by id, filtering by category,
    support checks and gap analysis.
    """

    capabilities: list[Capability] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    def __post_init__(self) -> None:
        # Build an id -> Capability index for O(1) lookups.
        self._index: dict[str, Capability] = {
            cap.capability_id: cap for cap in self.capabilities
        }

    def register(self, capability: Capability) -> None:
        """Add a capability to the manifest (backward compatible)."""
        self.capabilities.append(capability)
        self._index[capability.capability_id] = capability

    def register_many(self, capabilities: list[Capability]) -> None:
        """Register multiple capabilities at once (backward compatible)."""
        for cap in capabilities:
            self.register(cap)

    # ------------------------------------------------------------------
    # Query API (new, per the real-capability-manifest design)
    # ------------------------------------------------------------------

    def find(self, capability_id: str) -> Capability | None:
        """Return the :class:`Capability` with *capability_id*, or ``None``."""
        return self._index.get(capability_id)

    def find_by_category(self, category: str) -> list[Capability]:
        """Return all capabilities whose ``category`` matches."""
        return [cap for cap in self.capabilities if cap.category == category]

    def is_supported(self, capability_id: str) -> bool:
        """Return ``True`` if *capability_id* exists and is natively supported.

        A capability that is *known* but marked ``supported=False`` (or
        entirely unknown) returns ``False``.
        """
        cap = self._index.get(capability_id)
        return cap is not None and cap.supported

    def get_missing_capabilities(self, requested: list[str]) -> list[str]:
        """Return the subset of *requested* ids that are not supported.

        An id is "missing" when it is either unknown to the manifest or
        known but marked ``supported=False``.  The order of *requested*
        is preserved and duplicates are dropped.
        """
        missing: list[str] = []
        seen: set[str] = set()
        for cap_id in requested:
            if cap_id in seen:
                continue
            seen.add(cap_id)
            if not self.is_supported(cap_id):
                missing.append(cap_id)
        return missing

    # ------------------------------------------------------------------
    # Backward-compatible query API
    # ------------------------------------------------------------------
    #
    # These mirror the previous (non-dataclass) CapabilityManifest so
    # that existing consumers keep working.  ``has`` reports whether an
    # id is registered; ``get`` is an alias for :meth:`find`.

    def has(self, capability_id: str) -> bool:
        """Return ``True`` if *capability_id* is registered."""
        return capability_id in self._index

    def get(self, capability_id: str) -> Capability | None:
        """Return the :class:`Capability` for *capability_id*, or ``None``."""
        return self._index.get(capability_id)

    def list_by_category(self, category: str) -> list[Capability]:
        """Return all capabilities belonging to *category*."""
        return self.find_by_category(category)

    def all_ids(self) -> list[str]:
        """Return all registered capability IDs."""
        return list(self._index.keys())

    def __len__(self) -> int:
        return len(self._index)

    def __contains__(self, capability_id: object) -> bool:
        return isinstance(capability_id, str) and capability_id in self._index

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Serialize the whole manifest to a plain ``dict``."""
        return {
            "capabilities": [cap.to_dict() for cap in self.capabilities],
            "categories": sorted({cap.category for cap in self.capabilities}),
            "total": len(self.capabilities),
            "supported_count": sum(
                1 for cap in self.capabilities if cap.supported
            ),
        }


# ---------------------------------------------------------------------------
# Pre-registered capabilities
# ---------------------------------------------------------------------------


def _build_default_capabilities() -> list[Capability]:
    """Return the list of all natively supported capabilities.

    Grouped by category: geometry, material, physics, boundary,
    observable and postprocessing.  Every capability listed here is
    marked ``supported=True`` because the system ships with a compiler
    for it.
    """
    caps: list[Capability] = []

    # ------------------------------------------------------------------
    # Geometry (all supported)
    # ------------------------------------------------------------------
    caps.extend(
        [
            Capability(
                capability_id="geometry.circle",
                category="geometry",
                description="Circle / cylinder geometry approximated as a polygon",
                supported=True,
                required_properties=["radius", "center_x", "center_y"],
                compiler_hook="compile_circle",
                openfoam_config={
                    "representation": "circle",
                    "blockmesh_strategy": "polygon_approximation",
                    "default_segments": 16,
                    "stl_capable": True,
                    "geometry_type": "circle",
                },
            ),
            Capability(
                capability_id="geometry.rectangle",
                category="geometry",
                description="Axis-aligned rectangle geometry",
                supported=True,
                required_properties=["width", "height", "center_x", "center_y"],
                compiler_hook="compile_rectangle",
                openfoam_config={
                    "representation": "explicit_polygon",
                    "subtype": "axis_aligned",
                    "blockmesh_strategy": "single_hex_block",
                    "stl_capable": True,
                    "geometry_type": "rectangle",
                },
            ),
            Capability(
                capability_id="geometry.triangle",
                category="geometry",
                description="Triangle geometry sitting on the bottom wall",
                supported=True,
                required_properties=["base_width", "height", "center_x"],
                compiler_hook="compile_triangle",
                openfoam_config={
                    "representation": "explicit_polygon",
                    "subtype": "three_vertex",
                    "blockmesh_strategy": "polygon_vertices",
                    "stl_capable": True,
                    "geometry_type": "triangle",
                },
            ),
            Capability(
                capability_id="geometry.trapezoid",
                category="geometry",
                description="Trapezoid geometry sitting on the bottom wall",
                supported=True,
                required_properties=[
                    "top_width",
                    "bottom_width",
                    "height",
                    "center_x",
                ],
                compiler_hook="compile_trapezoid",
                openfoam_config={
                    "representation": "explicit_polygon",
                    "subtype": "four_vertex",
                    "blockmesh_strategy": "polygon_vertices",
                    "stl_capable": True,
                    "geometry_type": "trapezoid",
                },
            ),
            Capability(
                capability_id="geometry.cosine_bell",
                category="geometry",
                description="Cosine bell bump profile geometry",
                supported=True,
                required_properties=["width", "height", "center_x"],
                compiler_hook="compile_profile",
                openfoam_config={
                    "representation": "profile_function",
                    "subtype": "cosine",
                    "blockmesh_strategy": "profile_sampling",
                    "default_profile_points": 32,
                    "stl_capable": True,
                    "geometry_type": "cosine_bell",
                },
            ),
            Capability(
                capability_id="geometry.half_sine",
                category="geometry",
                description="Half sine bump profile geometry",
                supported=True,
                required_properties=["width", "height", "center_x"],
                compiler_hook="compile_profile",
                openfoam_config={
                    "representation": "profile_function",
                    "subtype": "half_sine",
                    "blockmesh_strategy": "profile_sampling",
                    "default_profile_points": 32,
                    "stl_capable": True,
                    "geometry_type": "half_sine",
                },
            ),
            Capability(
                capability_id="geometry.gaussian",
                category="geometry",
                description="Gaussian bump profile geometry",
                supported=True,
                required_properties=["width", "height", "center_x"],
                compiler_hook="compile_profile",
                openfoam_config={
                    "representation": "profile_function",
                    "subtype": "gaussian",
                    "blockmesh_strategy": "profile_sampling",
                    "default_profile_points": 32,
                    "stl_capable": True,
                    "geometry_type": "gaussian",
                },
            ),
            Capability(
                capability_id="geometry.ellipse",
                category="geometry",
                description="Ellipse geometry approximated as a polygon",
                supported=True,
                required_properties=[
                    "semi_axis_a",
                    "semi_axis_b",
                    "center_x",
                    "center_y",
                ],
                compiler_hook="compile_ellipse",
                openfoam_config={
                    "representation": "ellipse",
                    "blockmesh_strategy": "polygon_approximation",
                    "default_segments": 32,
                    "stl_capable": True,
                    "geometry_type": "ellipse",
                },
            ),
            Capability(
                capability_id="geometry.explicit_polygon",
                category="geometry",
                description="Any user-defined polygon from explicit vertices",
                supported=True,
                required_properties=["vertices"],
                compiler_hook="compile_polygon",
                openfoam_config={
                    "representation": "explicit_polygon",
                    "subtype": "parametric",
                    "blockmesh_strategy": "polygon_vertices",
                    "stl_capable": True,
                    "geometry_type": "explicit_polygon",
                },
            ),
        ]
    )

    # ------------------------------------------------------------------
    # Material (all supported)
    # ------------------------------------------------------------------
    caps.extend(
        [
            Capability(
                capability_id="material.incompressible_newtonian",
                category="material",
                description="Incompressible Newtonian fluid",
                supported=True,
                required_properties=["density", "viscosity"],
                compiler_hook="compile_incompressible_material",
                openfoam_config={
                    "physicalProperties": {
                        "rho": "uniform density",
                        "nu": "kinematic viscosity (mu/rho)",
                    },
                    "transport_model": "Newtonian",
                    "compressibility": "incompressible",
                },
            ),
            Capability(
                capability_id="material.compressible_newtonian",
                category="material",
                description="Compressible Newtonian fluid (ideal gas)",
                supported=True,
                required_properties=[
                    "density",
                    "viscosity",
                    "gas_constant",
                    "specific_heat_ratio",
                ],
                compiler_hook="compile_compressible_material",
                openfoam_config={
                    "physicalProperties": {
                        "rho": "density",
                        "mu": "dynamic viscosity",
                        "R": "specific gas constant",
                        "gamma": "specific heat ratio (Cp/Cv)",
                    },
                    "transport_model": "Newtonian",
                    "compressibility": "compressible",
                    "equation_of_state": "ideal_gas",
                },
            ),
        ]
    )

    # ------------------------------------------------------------------
    # Physics (supported)
    # ------------------------------------------------------------------
    caps.extend(
        [
            Capability(
                capability_id="physics.laminar",
                category="physics",
                description="Laminar flow (no turbulence model)",
                supported=True,
                required_properties=[],
                compiler_hook="compile_laminar",
                openfoam_config={
                    "momentumTransport": {
                        "simulationType": "laminar",
                    },
                    "solver_hint": "incompressibleFluid",
                },
            ),
            Capability(
                capability_id="physics.turbulent_k_omega_sst",
                category="physics",
                description="k-omega SST RANS turbulence model",
                supported=True,
                required_properties=[],
                compiler_hook="compile_k_omega_sst",
                openfoam_config={
                    "momentumTransport": {
                        "simulationType": "RAS",
                        "RAS": {
                            "model": "kOmegaSST",
                        },
                    },
                    "wall_function": "available",
                    "solver_hint": "incompressibleFluid",
                },
            ),
            Capability(
                capability_id="physics.turbulent_k_epsilon",
                category="physics",
                description="k-epsilon RANS turbulence model",
                supported=True,
                required_properties=[],
                compiler_hook="compile_k_epsilon",
                openfoam_config={
                    "momentumTransport": {
                        "simulationType": "RAS",
                        "RAS": {
                            "model": "kEpsilon",
                        },
                    },
                    "wall_function": "available",
                    "solver_hint": "incompressibleFluid",
                },
            ),
            Capability(
                capability_id="physics.large_eddy_simulation",
                category="physics",
                description="Large Eddy Simulation (LES)",
                supported=True,
                required_properties=[],
                compiler_hook="compile_les",
                openfoam_config={
                    "momentumTransport": {
                        "simulationType": "LES",
                        "LES": {
                            "model": "Smagorinsky",
                            "delta": "cubeRootVol",
                        },
                    },
                    "solver_hint": "incompressibleFluid",
                    "notes": "requires fine mesh and small time step",
                },
            ),
        ]
    )

    # ------------------------------------------------------------------
    # Boundary (all supported)
    # ------------------------------------------------------------------
    caps.extend(
        [
            Capability(
                capability_id="boundary.velocity_inlet",
                category="boundary",
                description="Fixed velocity inlet boundary condition",
                supported=True,
                required_properties=["velocity"],
                compiler_hook="compile_velocity_inlet",
                openfoam_config={
                    "U": {"type": "fixedValue", "value": "uniform velocity"},
                    "p": {"type": "zeroGradient"},
                },
            ),
            Capability(
                capability_id="boundary.pressure_outlet",
                category="boundary",
                description="Fixed pressure outlet boundary condition",
                supported=True,
                required_properties=["pressure"],
                compiler_hook="compile_pressure_outlet",
                openfoam_config={
                    "p": {"type": "fixedValue", "value": "uniform pressure"},
                    "U": {"type": "inletOutlet"},
                },
            ),
            Capability(
                capability_id="boundary.no_slip_wall",
                category="boundary",
                description="Standard no-slip wall boundary condition",
                supported=True,
                required_properties=[],
                compiler_hook="compile_no_slip_wall",
                openfoam_config={
                    "U": {"type": "noSlip"},
                    "p": {"type": "zeroGradient"},
                },
            ),
            Capability(
                capability_id="boundary.slip_wall",
                category="boundary",
                description="Slip wall boundary condition",
                supported=True,
                required_properties=[],
                compiler_hook="compile_slip_wall",
                openfoam_config={
                    "U": {"type": "slip"},
                    "p": {"type": "zeroGradient"},
                },
            ),
            Capability(
                capability_id="boundary.symmetry",
                category="boundary",
                description="Symmetry plane boundary condition",
                supported=True,
                required_properties=[],
                compiler_hook="compile_symmetry",
                openfoam_config={
                    "U": {"type": "symmetry"},
                    "p": {"type": "symmetry"},
                },
            ),
            Capability(
                capability_id="boundary.periodic",
                category="boundary",
                description="Periodic / cyclic boundary condition pair",
                supported=True,
                required_properties=[],
                compiler_hook="compile_periodic",
                openfoam_config={
                    "U": {"type": "cyclic"},
                    "p": {"type": "cyclic"},
                    "requires_patch_pair": True,
                },
            ),
            Capability(
                capability_id="boundary.shear_stress",
                category="boundary",
                description="Applied wall shear stress boundary condition",
                supported=True,
                required_properties=["shear_stress"],
                compiler_hook="compile_shear_stress",
                openfoam_config={
                    "U": {
                        "type": "wallShearStress",
                        "value": "uniform shear_stress",
                    },
                    "p": {"type": "zeroGradient"},
                },
            ),
            Capability(
                capability_id="boundary.mass_flow_inlet",
                category="boundary",
                description="Mass flow rate inlet boundary condition",
                supported=True,
                required_properties=["mass_flow_rate"],
                compiler_hook="compile_mass_flow_inlet",
                openfoam_config={
                    "U": {"type": "flowRateInletVelocity"},
                    "p": {"type": "zeroGradient"},
                    "phi": "mass_flow_rate",
                },
            ),
        ]
    )

    # ------------------------------------------------------------------
    # Observable (supported)
    # ------------------------------------------------------------------
    caps.extend(
        [
            Capability(
                capability_id="observable.drag_coefficient",
                category="observable",
                description="Drag coefficient (Cd)",
                supported=True,
                required_properties=[],
                compiler_hook="compile_force_coefficients",
                openfoam_config={
                    "functionObject": "forceCoeffs",
                    "fields": ["U", "p"],
                    "coefficients": ["Cd"],
                    "requires_patches": True,
                },
            ),
            Capability(
                capability_id="observable.lift_coefficient",
                category="observable",
                description="Lift coefficient (Cl)",
                supported=True,
                required_properties=[],
                compiler_hook="compile_force_coefficients",
                openfoam_config={
                    "functionObject": "forceCoeffs",
                    "fields": ["U", "p"],
                    "coefficients": ["Cl"],
                    "requires_patches": True,
                },
            ),
            Capability(
                capability_id="observable.strouhal_number",
                category="observable",
                description="Strouhal number (St) from vortex shedding",
                supported=True,
                required_properties=[],
                compiler_hook="compile_strouhal",
                openfoam_config={
                    "functionObject": "probes",
                    "derived_from": "lift_coefficient",
                    "statistic": "dominant_shedding_frequency",
                    "requires_transient": True,
                },
            ),
            Capability(
                capability_id="observable.velocity_field",
                category="observable",
                description="Full velocity field output",
                supported=True,
                required_properties=[],
                compiler_hook="compile_field_output",
                openfoam_config={
                    "functionObject": "volFieldValue",
                    "fields": ["U"],
                    "write_interval": "configurable",
                },
            ),
            Capability(
                capability_id="observable.pressure_field",
                category="observable",
                description="Full pressure field output",
                supported=True,
                required_properties=[],
                compiler_hook="compile_field_output",
                openfoam_config={
                    "functionObject": "volFieldValue",
                    "fields": ["p"],
                    "write_interval": "configurable",
                },
            ),
            Capability(
                capability_id="observable.vorticity",
                category="observable",
                description="Vorticity field computation",
                supported=True,
                required_properties=[],
                compiler_hook="compile_vorticity",
                openfoam_config={
                    "functionObject": "vorticity",
                    "fields": ["U"],
                    "operation": "curl",
                },
            ),
            Capability(
                capability_id="observable.section_mean_velocity",
                category="observable",
                description="Cross-section mean velocity",
                supported=True,
                required_properties=[],
                compiler_hook="compile_section_mean_velocity",
                openfoam_config={
                    "functionObject": "surfaceFieldValue",
                    "fields": ["U"],
                    "operation": "areaAverage",
                    "requires_surface": True,
                },
            ),
        ]
    )

    # ------------------------------------------------------------------
    # Postprocessing (supported)
    # ------------------------------------------------------------------
    caps.extend(
        [
            Capability(
                capability_id="postprocessing.streamlines",
                category="postprocessing",
                description="Streamline visualization",
                supported=True,
                required_properties=[],
                compiler_hook="compile_streamlines",
                openfoam_config={
                    "tool": "paraFoam",
                    "representation": "streamlines",
                    "fields": ["U"],
                    "seed_strategy": "configurable",
                },
            ),
            Capability(
                capability_id="postprocessing.contour",
                category="postprocessing",
                description="Contour plots of scalar fields",
                supported=True,
                required_properties=[],
                compiler_hook="compile_contour",
                openfoam_config={
                    "tool": "paraFoam",
                    "representation": "contour",
                    "fields": ["p", "U", "vorticity"],
                },
            ),
            Capability(
                capability_id="postprocessing.force_coefficients",
                category="postprocessing",
                description="Force coefficient time history (Cd/Cl vs time)",
                supported=True,
                required_properties=[],
                compiler_hook="compile_force_coefficient_history",
                openfoam_config={
                    "source": "forceCoeffs",
                    "output": "time_series",
                    "fields": ["Cd", "Cl"],
                    "requires_transient": True,
                },
            ),
        ]
    )

    return caps


#: The process-wide default :class:`CapabilityManifest`, pre-populated
#: with every natively supported capability.  Use
#: :func:`get_default_manifest` to access it.
DEFAULT_MANIFEST: CapabilityManifest = CapabilityManifest(
    capabilities=_build_default_capabilities()
)


def get_default_manifest() -> CapabilityManifest:
    """Return the default, pre-populated :class:`CapabilityManifest`.

    This is the canonical entry point for consumers that need the
    system's real capability registry.  The returned manifest is the
    shared :data:`DEFAULT_MANIFEST` singleton.
    """
    return DEFAULT_MANIFEST


__all__ = [
    "Capability",
    "CapabilityManifest",
    "DEFAULT_MANIFEST",
    "get_default_manifest",
]
