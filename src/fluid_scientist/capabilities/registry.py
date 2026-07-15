"""Unified, persistent Capability Registry with native capability registration.

The registry holds both built-in (native) capabilities and dynamically
generated code extensions.  It is queried by the CapabilityResolver to
determine whether a design can be compiled natively or whether an
extension must be generated.
"""

from __future__ import annotations

import hashlib
import importlib
import inspect
from typing import Any, Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Capability types (exhaustive list across the pipeline)
# ---------------------------------------------------------------------------

CAPABILITY_TYPES = Literal[
    "geometry_generator",
    "domain_generator",
    "mesh_generator",
    "motion_compiler",
    "physics_model_compiler",
    "boundary_writer",
    "initial_condition_writer",
    "solver_adapter",
    "function_object_generator",
    "field_sampler",
    "postprocessor",
    "result_validator",
    "workstation_executor",
]


# ---------------------------------------------------------------------------
# CapabilityStatus
# ---------------------------------------------------------------------------

class CapabilityStatus:
    DECLARED = "declared"
    IMPLEMENTED = "implemented"
    TESTED = "tested"
    REGISTERED = "registered"
    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    DEPRECATED = "deprecated"


class CapabilityHealthIssue(BaseModel):
    """One concrete health-check issue for a registered capability."""

    capability_id: str
    severity: Literal["error", "warning", "info"] = "error"
    issue_code: str
    message: str


class CapabilityHealthRecord(BaseModel):
    """Health state for one capability registry entry."""

    capability_id: str
    capability_type: str
    status_before: str
    status_after: str
    implementation_entrypoint: str = ""
    implementation_hash: str = ""
    issues: list[CapabilityHealthIssue] = Field(default_factory=list)

    @property
    def healthy(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)


class CapabilityHealthReport(BaseModel):
    """Registry-wide health check report."""

    total: int = 0
    verified: int = 0
    unverified: int = 0
    degraded: int = 0
    records: list[CapabilityHealthRecord] = Field(default_factory=list)

    @property
    def healthy(self) -> bool:
        return self.unverified == 0


# ---------------------------------------------------------------------------
# Capability  (typed, serializable)
# ---------------------------------------------------------------------------


class Capability(BaseModel):
    """A single registered capability."""

    capability_id: str
    capability_type: str
    name: str = ""
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    supported_versions: list[str] = Field(default_factory=list)
    implementation_entrypoint: str = ""
    implementation_module: str = ""
    implementation_hash: str = ""
    test_manifest: list[str] = Field(default_factory=list)
    verification_artifact: str = ""
    tests: list[str] = Field(default_factory=list)
    status: str = CapabilityStatus.REGISTERED
    is_native: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    def can_handle(self, requirement: dict[str, Any]) -> bool:
        """Check if this capability satisfies a requirement dict."""
        req_type = requirement.get("capability_type")
        if req_type and req_type != self.capability_type:
            return False
        req_id = requirement.get("capability_id")
        if req_id:
            return req_id == self.capability_id
        # Pattern matching on keywords
        keywords = requirement.get("keywords", [])
        if keywords:
            desc_lower = (self.name + " " + self.description + " " + self.capability_id).lower()
            return any(kw.lower() in desc_lower for kw in keywords)
        return True


# ---------------------------------------------------------------------------
# Capability Requirement (output of the resolver)
# ---------------------------------------------------------------------------


class CapabilityRequirement(BaseModel):
    """A requirement for a specific capability."""

    requirement_id: str
    capability_type: str
    capability_id: str = ""
    description: str = ""
    keywords: list[str] = Field(default_factory=list)
    required_input: dict[str, Any] = Field(default_factory=dict)
    expected_output: dict[str, Any] = Field(default_factory=dict)
    required_by: list[str] = Field(default_factory=list)
    input_contract: dict[str, Any] = Field(default_factory=dict)
    output_contract: dict[str, Any] = Field(default_factory=dict)
    scientific_reason: str = ""
    openfoam_mapping: dict[str, Any] = Field(default_factory=dict)
    fallback_options: list[dict[str, Any]] = Field(default_factory=list)
    mandatory: bool = True
    satisfied_by: str | None = None
    extension_needed: bool = False


# ---------------------------------------------------------------------------
# Native capabilities  (registered at startup)
# ---------------------------------------------------------------------------

def _build_native_capabilities() -> list[Capability]:
    """Return the list of built-in native capabilities.

    These cover the common OpenFOAM workflows that ship with the system.
    Additional capabilities can be added via the code-extension pipeline.
    """
    caps: list[Capability] = []

    # ---- geometry generators ----
    caps.append(Capability(
        capability_id="geometry.block_mesh_rectangular",
        capability_type="geometry_generator",
        name="BlockMesh Rectangular Domain",
        description="Generate rectangular/hexahedral blockMesh geometries with optional internal bodies",
        implementation_entrypoint="fluid_scientist.case_generation.geometry:block_mesh_rectangular",
        is_native=True,
        status=CapabilityStatus.VERIFIED,
        supported_versions=["openfoam13"],
    ))
    caps.append(Capability(
        capability_id="geometry.cylinder_in_channel",
        capability_type="geometry_generator",
        name="Cylinder in Channel",
        description="Generate cylinder-in-crossflow geometry via blockMesh",
        implementation_entrypoint="fluid_scientist.case_generation.geometry:cylinder_in_channel",
        is_native=True,
        status=CapabilityStatus.VERIFIED,
    ))
    caps.append(Capability(
        capability_id="geometry.pipe",
        capability_type="geometry_generator",
        name="Pipe (circular duct)",
        description="Generate pipe geometry (wedge 2D or full 3D)",
        implementation_entrypoint="fluid_scientist.case_generation.geometry:pipe_geometry",
        is_native=True,
        status=CapabilityStatus.VERIFIED,
    ))

    # ---- mesh generators ----
    caps.append(Capability(
        capability_id="mesh.block_mesh",
        capability_type="mesh_generator",
        name="blockMesh Generator",
        description="Run blockMesh to generate a hexahedral mesh",
        implementation_entrypoint="fluid_scientist.case_generation.mesh:run_block_mesh",
        is_native=True,
        status=CapabilityStatus.VERIFIED,
    ))
    caps.append(Capability(
        capability_id="mesh.snappy_hex_mesh",
        capability_type="mesh_generator",
        name="snappyHexMesh Generator",
        description="Run snappyHexMesh for body-fitted meshes around triangulated surfaces",
        implementation_entrypoint="fluid_scientist.case_generation.mesh:run_snappy_hex_mesh",
        is_native=True,
        status=CapabilityStatus.REGISTERED,
    ))

    # ---- motion compilers ----
    caps.append(Capability(
        capability_id="motion.static",
        capability_type="motion_compiler",
        name="Static Mesh (no motion)",
        description="No mesh motion -- all walls static",
        implementation_entrypoint="fluid_scientist.case_generation.motion:static_mesh",
        is_native=True,
        status=CapabilityStatus.VERIFIED,
    ))
    caps.append(Capability(
        capability_id="motion.rotating_wall",
        capability_type="motion_compiler",
        name="Rotating Wall (MRF/solid body)",
        description="Compile rotating wall boundary conditions and MRF properties",
        implementation_entrypoint="fluid_scientist.case_generation.motion:rotating_wall",
        is_native=True,
        status=CapabilityStatus.REGISTERED,
    ))
    caps.append(Capability(
        capability_id="motion.oscillating",
        capability_type="motion_compiler",
        name="Oscillating Motion",
        description="Compile oscillating/pitching rigid body motion via dynamic mesh",
        implementation_entrypoint="fluid_scientist.case_generation.motion:oscillating_motion",
        is_native=True,
        status=CapabilityStatus.REGISTERED,
    ))

    # ---- physics model compilers ----
    caps.append(Capability(
        capability_id="physics.incompressible_single_phase",
        capability_type="physics_model_compiler",
        name="Incompressible Single-Phase Flow",
        description="Compile physicalProperties and momentumTransport for incompressible single-phase flow",
        implementation_entrypoint="fluid_scientist.case_generation.physics:incompressible_single_phase",
        is_native=True,
        status=CapabilityStatus.VERIFIED,
    ))
    caps.append(Capability(
        capability_id="physics.smagorinsky_les",
        capability_type="physics_model_compiler",
        name="Smagorinsky LES",
        description="Smagorinsky LES turbulence model",
        implementation_entrypoint="fluid_scientist.case_generation.physics:smagorinsky_les",
        is_native=True,
        status=CapabilityStatus.VERIFIED,
    ))
    caps.append(Capability(
        capability_id="physics.wale_les",
        capability_type="physics_model_compiler",
        name="WALE LES",
        description="WALE LES turbulence model",
        implementation_entrypoint="fluid_scientist.case_generation.physics:wale_les",
        is_native=True,
        status=CapabilityStatus.REGISTERED,
    ))
    caps.append(Capability(
        capability_id="physics.komegasst_rans",
        capability_type="physics_model_compiler",
        name="k-omega SST RANS",
        description="k-omega SST RANS turbulence model",
        implementation_entrypoint="fluid_scientist.case_generation.physics:komegasst_rans",
        is_native=True,
        status=CapabilityStatus.VERIFIED,
    ))
    caps.append(Capability(
        capability_id="physics.laminar",
        capability_type="physics_model_compiler",
        name="Laminar Flow",
        description="Laminar (no turbulence model)",
        implementation_entrypoint="fluid_scientist.case_generation.physics:laminar",
        is_native=True,
        status=CapabilityStatus.VERIFIED,
    ))

    # ---- boundary writers ----
    for bc_id, name, desc in [
        ("boundary.no_slip_wall", "No-slip Wall", "Standard no-slip wall BC for U and p"),
        ("boundary.moving_wall_velocity", "Moving Wall (fixed velocity)", "Moving wall with specified constant velocity"),
        ("boundary.free_slip_wall", "Free-slip Wall", "Slip wall BC (symmetry-like for walls)"),
        ("boundary.symmetry", "Symmetry Plane", "Symmetry plane boundary condition"),
        ("boundary.velocity_inlet", "Velocity Inlet", "Fixed velocity inlet with optional turbulence specification"),
        ("boundary.pressure_outlet", "Pressure Outlet", "Fixed pressure outlet (fixedValue for p)"),
        ("boundary.advective_outlet", "Advective Outlet", "Advective (convective) outlet for transient flows"),
        ("boundary.periodic_cyclic", "Periodic/Cyclic", "Cyclic/periodic boundary pairs"),
        ("boundary.empty_2d", "Empty (2D)", "Empty boundary for 2D simulations"),
        ("boundary.inlet_outlet", "Inlet-Outlet", "inletOutlet for conditions with potential backflow"),
    ]:
        caps.append(Capability(
            capability_id=bc_id,
            capability_type="boundary_writer",
            name=name,
            description=desc,
            implementation_entrypoint=f"fluid_scientist.case_generation.boundaries:{bc_id.split('.')[-1]}",
            is_native=True,
            status=CapabilityStatus.VERIFIED,
        ))

    # ---- initial condition writers ----
    caps.append(Capability(
        capability_id="ic.uniform_fields",
        capability_type="initial_condition_writer",
        name="Uniform Initial Fields",
        description="Write uniform initial conditions for U, p, and turbulence fields",
        implementation_entrypoint="fluid_scientist.case_generation.initial_conditions:uniform_fields",
        is_native=True,
        status=CapabilityStatus.VERIFIED,
    ))
    caps.append(Capability(
        capability_id="ic.potential_flow",
        capability_type="initial_condition_writer",
        name="Potential Flow Initialization",
        description="Initialize with potentialFoam solution",
        implementation_entrypoint="fluid_scientist.case_generation.initial_conditions:potential_flow_init",
        is_native=True,
        status=CapabilityStatus.REGISTERED,
    ))

    # ---- solver adapters ----
    for solver_id, name, desc in [
        ("solver.incompressible_fluid", "incompressibleFluid", "Foundation 13 incompressible flow solver module (foamRun -solver incompressibleFluid)"),
        ("solver.rhopimplefoam", "rhoPimpleFoam", "Transient compressible flow solver"),
    ]:
        caps.append(Capability(
            capability_id=solver_id,
            capability_type="solver_adapter",
            name=name,
            description=desc,
            implementation_entrypoint=f"fluid_scientist.case_generation.solvers:{solver_id.split('.')[-1]}",
            is_native=True,
            status=CapabilityStatus.VERIFIED,
        ))

    # ---- function object generators ----
    for fo_id, name, desc in [
        ("fo.residuals", "Residuals", "Monitor solver residuals via functionObject"),
        ("fo.force_coeffs", "Force Coefficients", "Compute forces and force coefficients (drag/lift)"),
        ("fo.forces", "Forces", "Compute forces on patches"),
        ("fo.probes", "Probes", "Sample field values at point locations"),
        ("fo.sampled_surfaces", "Sampled Surfaces", "Sample fields on surfaces (cuts, planes)"),
        ("fo.sampled_sets", "Sampled Sets", "Sample fields along lines/sets"),
        ("fo.field_average", "Field Averaging", "Compute time-averaged fields"),
        ("fo.volume_field_output", "Volume Field Output", "Write volume fields at intervals"),
        ("fo.surface_field_output", "Surface Field Output", "Write surface fields at intervals"),
        ("fo.courant_no", "Courant Number", "Compute and output Courant number field"),
        ("fo.y_plus", "y+", "Compute y+ on wall patches"),
        ("fo.q_criterion", "Q-Criterion", "Compute Q-criterion for vortex identification"),
        ("fo.lambda2", "Lambda2", "Compute lambda2 criterion for vortex identification"),
    ]:
        caps.append(Capability(
            capability_id=fo_id,
            capability_type="function_object_generator",
            name=name,
            description=desc,
            implementation_entrypoint=f"fluid_scientist.case_generation.function_objects:{fo_id.split('.')[-1]}",
            is_native=True,
            status=CapabilityStatus.VERIFIED,
        ))

    # ---- field samplers ----
    caps.append(Capability(
        capability_id="sampler.basic",
        capability_type="field_sampler",
        name="Basic Field Sampler",
        description="Sample U, p fields at probes and surfaces",
        implementation_entrypoint="fluid_scientist.case_generation.sampling:basic_sampler",
        is_native=True,
        status=CapabilityStatus.VERIFIED,
    ))

    # ---- postprocessors ----
    for pp_id, name, desc in [
        ("postprocess.force_spectrum", "Force Spectrum (PSD)", "Compute power spectral density of force coefficients"),
        ("postprocess.velocity_profile", "Velocity Profile", "Extract and plot velocity profiles"),
        ("postprocess.wake_centerline", "Wake Centerline", "Track wake centerline and deflection"),
        ("postprocess.pressure_drop", "Pressure Drop", "Compute pressure drop between inlet and outlet"),
        ("postprocess.flow_rate", "Flow Rate", "Compute volumetric/mass flow rate through patches"),
        ("postprocess.statistics", "Turbulence Statistics", "Compute mean, RMS, Reynolds stresses"),
        ("postprocess.vortex_tracking", "Vortex Identification", "Identify and track vortical structures (Q/lambda2)"),
        ("postprocess.mesh_check", "Mesh Quality Report", "Summarize checkMesh output"),
        ("postprocess.residual_analysis", "Residual Convergence", "Analyze residual convergence history"),
        ("postprocess.conservation", "Conservation Check", "Check mass/momentum conservation"),
    ]:
        caps.append(Capability(
            capability_id=pp_id,
            capability_type="postprocessor",
            name=name,
            description=desc,
            implementation_entrypoint=f"fluid_scientist.case_generation.postprocessors:{pp_id.split('.')[-1]}",
            is_native=True,
            status=CapabilityStatus.REGISTERED,
        ))

    # ---- result validators ----
    for v_id, name, desc in [
        ("validator.dictionary_parse", "OpenFOAM Dictionary Parsing", "Validate that generated dictionaries parse correctly"),
        ("validator.patch_consistency", "Patch Name Consistency", "Validate that BC patches exist in the mesh"),
        ("validator.check_mesh", "checkMesh Validation", "Run checkMesh and verify mesh quality"),
        ("validator.solver_dry_run", "Solver Dry-Run", "Run solver for 0-1 timesteps to verify case setup"),
        ("validator.function_object_output", "Function Object Output", "Verify functionObjects produce expected outputs"),
        ("validator.boundary_verification", "Boundary Verification", "Verify boundary conditions are physically consistent"),
    ]:
        caps.append(Capability(
            capability_id=v_id,
            capability_type="result_validator",
            name=name,
            description=desc,
            implementation_entrypoint=f"fluid_scientist.case_generation.validators:{v_id.split('.')[-1]}",
            is_native=True,
            status=CapabilityStatus.VERIFIED if v_id in ("validator.dictionary_parse", "validator.patch_consistency") else CapabilityStatus.REGISTERED,
        ))

    return caps


# ---------------------------------------------------------------------------
# CapabilityRegistry
# ---------------------------------------------------------------------------


class CapabilityRegistry:
    """Unified in-memory + persistable capability registry."""

    def __init__(self) -> None:
        self._capabilities: dict[str, Capability] = {}
        self._register_natives()

    def _register_natives(self) -> None:
        for cap in _build_native_capabilities():
            self._capabilities[cap.capability_id] = cap

    def register(self, capability: Capability) -> None:
        """Register a new capability (typically from code extension)."""
        self._capabilities[capability.capability_id] = capability

    def health_check(self, *, mutate: bool = False) -> CapabilityHealthReport:
        """Validate registry entries against their implementation contracts.

        The check is intentionally concrete: a VERIFIED capability must expose
        an importable ``module:function`` entrypoint, carry a stable
        implementation hash, and keep verification evidence in the registry.
        Any error degrades the capability to UNVERIFIED when ``mutate`` is true.
        """
        records: list[CapabilityHealthRecord] = []
        degraded = 0

        for cap in self._capabilities.values():
            issues: list[CapabilityHealthIssue] = []
            implementation_hash = cap.implementation_hash
            status_before = cap.status

            if cap.status == CapabilityStatus.VERIFIED:
                if not cap.implementation_entrypoint:
                    issues.append(CapabilityHealthIssue(
                        capability_id=cap.capability_id,
                        issue_code="missing_entrypoint",
                        message="VERIFIED capability has no implementation_entrypoint.",
                    ))
                else:
                    hash_value, entrypoint_issues = self._inspect_entrypoint(cap)
                    implementation_hash = hash_value or implementation_hash
                    issues.extend(entrypoint_issues)

                if not cap.tests and not cap.test_manifest:
                    issues.append(CapabilityHealthIssue(
                        capability_id=cap.capability_id,
                        issue_cod