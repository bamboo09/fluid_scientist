"""ObstacleFlowOpenFOAM13Compiler — deterministic OpenFOAM Foundation 13 compiler.

Implements Section 18 of the plan.  Reads ObstacleFlowExperimentSpecV1
and deterministically generates all OpenFOAM case files.

Key constraints:
  - Uses foamRun + incompressibleFluid (not legacy pimpleFoam)
  - Uses physicalProperties (not transportProperties)
  - Uses momentumTransport for turbulence model
  - Pressure gradient via fvModels (Foundation 13 syntax)
  - 2D via empty front/back patches
  - All files are deterministic (no timestamps, sorted keys)
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import math
import tarfile
from dataclasses import dataclass, field
from typing import Any

from fluid_scientist.obstacle_flow.boundary_validator import BoundaryCombinationValidator
from fluid_scientist.obstacle_flow.geometry import (
    BumpProfileGenerator,
    CylinderGeometryBuilder,
    TrapezoidGeometry,
    TrapezoidGeometryBuilder,
)
from fluid_scientist.obstacle_flow.mesh import ObstacleFlowMeshBackend
from fluid_scientist.obstacle_flow.models import (
    BoundaryType,
    BumpSpec,
    CylinderBoundaryType,
    CylinderSpec,
    DomainSpec,
    FlowMode,
    FluidSpec,
    InletProfileSpec,
    ObservableSpec,
    ObservableType,
    ObstacleFlowExperimentSpecV1,
    PlotRequest,
    PolygonSpec,
    PressureGradientUnit,
    SimulationSpec,
    TemporalType,
    TrapezoidSpec,
    TurbulenceModel,
)


# ---------------------------------------------------------------------------
# Compiled case data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ObstacleFlowCompiledCase:
    """Compiled OpenFOAM case for obstacle flow."""

    files: dict[str, str]
    experiment_type: str = "obstacle_flow_2d"
    preprocessing: tuple[str, ...] = ("blockMesh", "snappyHexMesh", "checkMesh")
    required_outputs: tuple[str, ...] = ("velocity", "pressure", "residuals")
    spec_version: int = 1
    archive: bytes = b""
    archive_sha256: str = ""

    @property
    def digest(self) -> str:
        return self.archive_sha256


@dataclass(frozen=True)
class CompilationManifest:
    """Manifest tracking spec version and compilation result."""

    compilation_id: str
    spec_version: int
    spec_hash: str
    case_hash: str
    generated_files: list[str]
    compiler_id: str
    compiler_version: str
    flow_mode: str
    has_cylinder: bool
    has_bump: bool


class CompilationError(ValueError):
    """Raised when the spec cannot be compiled into a valid case."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fmt(v: float) -> str:
    """Format a float for OpenFOAM output."""
    return f"{v:.12g}"


def _header(class_name: str, object_name: str) -> str:
    """Generate an OpenFOAM file header."""
    return f"""/*--------------------------------*- C++ -*----------------------------------*\\
| =========                 |                                                 |
| \\\\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox           |
|  \\\\    /   O peration     | Version:  13                                    |
|   \\\\  /    A nd           | Web:      www.openfoam.org                      |
|    \\/     M anipulation  |                                                 |
\\*---------------------------------------------------------------------------*/
FoamFile
{{
    version     2.0;
    format      ascii;
    class       {class_name};
    object      {object_name};
}}
// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //
"""


def _normalize(text: str) -> str:
    """Normalize text for deterministic archiving."""
    if not text.endswith("\n"):
        text = text + "\n"
    return text.replace("\r\n", "\n")


def _deterministic_tar_gz(files: dict[str, str]) -> bytes:
    """Create a deterministic tar.gz archive from file contents.

    Every member carries a fixed mtime/uid/gid/uname/gname and mode, gzip is
    produced with a frozen header (``filename=""``, ``mtime=0``), and directory
    entries are emitted explicitly for every parent path (e.g. a file named
    ``constant/triSurface/geometry.stl`` yields ``constant/`` and
    ``constant/triSurface/``). Two runs over the same input therefore produce
    byte-identical output.
    """

    def _tar_info(name: str, *, mode: int, is_dir: bool) -> tarfile.TarInfo:
        info = tarfile.TarInfo(name)
        info.type = tarfile.DIRTYPE if is_dir else tarfile.REGTYPE
        info.size = 0
        info.mode = mode
        info.uid = 0
        info.gid = 0
        info.uname = ""
        info.gname = ""
        info.mtime = 0
        return info

    tar_output = io.BytesIO()
    with tarfile.open(fileobj=tar_output, mode="w", format=tarfile.USTAR_FORMAT) as bundle:
        added_dirs: set[str] = set()
        for name in sorted(files):
            segments = [segment for segment in name.split("/") if segment]
            for depth in range(1, len(segments)):
                dir_name = "/".join(segments[:depth]) + "/"
                if dir_name in added_dirs:
                    continue
                added_dirs.add(dir_name)
                bundle.addfile(_tar_info(dir_name, mode=0o755, is_dir=True))
            if name.endswith("/"):
                if name not in added_dirs:
                    added_dirs.add(name)
                    bundle.addfile(_tar_info(name, mode=0o755, is_dir=True))
                continue
            payload = files[name].encode("utf-8")
            info = _tar_info(name, mode=0o644, is_dir=False)
            info.size = len(payload)
            bundle.addfile(info, io.BytesIO(payload))
    compressed = io.BytesIO()
    with gzip.GzipFile(fileobj=compressed, mode="wb", filename="", mtime=0) as stream:
        stream.write(tar_output.getvalue())
    return compressed.getvalue()


# ---------------------------------------------------------------------------
# Main Compiler
# ---------------------------------------------------------------------------


class ObstacleFlowCompiler:
    """Compiles ObstacleFlowExperimentSpecV1 into OpenFOAM Foundation 13 case files.

    This is the main entry point — it orchestrates geometry validation,
    mesh generation, boundary compilation, and OpenFOAM dictionary generation.
    """

    compiler_id = "fluid_scientist.obstacle_flow.compiler_v1"
    compiler_version = "1.0.0"

    def __init__(self) -> None:
        self._mesh_backend = ObstacleFlowMeshBackend()
        self._boundary_validator = BoundaryCombinationValidator()
        self._bump_gen = BumpProfileGenerator()
        self._cyl_builder = CylinderGeometryBuilder()
        self._trap_builder = TrapezoidGeometryBuilder()

    def compile(
        self, spec: ObstacleFlowExperimentSpecV1
    ) -> tuple[ObstacleFlowCompiledCase, CompilationManifest]:
        """Compile the spec into OpenFOAM case files.

        Raises CompilationError if the spec is invalid or incomplete.
        """
        # Validate boundary combinations
        self._boundary_validator.validate(spec)

        # Validate geometry feasibility
        from fluid_scientist.obstacle_flow.models import GeometryFeasibilityValidator

        geom_validator = GeometryFeasibilityValidator()
        geom_validator.validate(spec)

        # Validate material consistency (fluid type vs density/viscosity)
        self._validate_material_consistency(spec)

        # Validate rotating cylinder has non-zero angular velocity
        self._validate_rotating_cylinder(spec)

        # Validate observable-geometry consistency (e.g. cylinder drag
        # requires a cylinder to be present)
        self._validate_observable_geometry(spec)

        # Generate mesh files
        mesh_manifest = self._mesh_backend.generate(spec)

        # Generate OpenFOAM case files
        files: dict[str, str] = {}

        # Mesh files
        files["system/blockMeshDict"] = mesh_manifest.block_mesh_dict
        if mesh_manifest.snappy_hex_mesh_dict is not None:
            files["system/snappyHexMeshDict"] = mesh_manifest.snappy_hex_mesh_dict
        if mesh_manifest.cylinder_stl is not None:
            files["constant/triSurface/cylinder.stl"] = mesh_manifest.cylinder_stl
        if mesh_manifest.rectangle_stl is not None:
            files["constant/triSurface/rectangle.stl"] = mesh_manifest.rectangle_stl
        if mesh_manifest.triangle_stl is not None:
            files["constant/triSurface/triangle.stl"] = mesh_manifest.triangle_stl
        # Trapezoid geometry (if present)
        if spec.has_trapezoid:
            trap_geom = self._trap_builder.build(spec.trapezoids[0])
        if mesh_manifest.trapezoid_stl is not None:
            files["constant/triSurface/trapezoid.stl"] = mesh_manifest.trapezoid_stl
        # Polygon geometry (if present)
        if mesh_manifest.polygon_stl is not None:
            files["constant/triSurface/polygon.stl"] = mesh_manifest.polygon_stl

        # Field files
        is_turbulent = spec.is_turbulent
        files["0/U"] = self._compile_velocity_field(spec)
        files["0/p"] = self._compile_pressure_field(spec)

        if is_turbulent:
            files["0/k"] = self._compile_tke_field(spec)
            files["0/omega"] = self._compile_omega_field(spec)
            files["0/nut"] = self._compile_nut_field(spec)

        # Constant files
        files["constant/physicalProperties"] = self._compile_physical_properties(spec)
        files["constant/momentumTransport"] = self._compile_momentum_transport(spec)

        # fvModels for pressure gradient / body force
        fv_models = self._compile_fv_models(spec)
        if fv_models is not None:
            files["system/fvModels"] = fv_models

        # System files
        files["system/controlDict"] = self._compile_control_dict(spec)
        files["system/fvSchemes"] = self._compile_fv_schemes(spec, is_turbulent)
        files["system/fvSolution"] = self._compile_fv_solution(spec, is_turbulent)
        files["system/decomposeParDict"] = self._compile_decompose_par_dict(spec)

        # Time-varying inlet velocity table (for sinusoidal / ramp / tabulated)
        inlet_table = self._compile_inlet_velocity_table(spec)
        if inlet_table is not None:
            files["constant/inletVelocity.table"] = inlet_table

        # Spec metadata
        metadata = {
            "schema_version": spec.schema_version,
            "case_family": spec.case_family,
            "spec_version": spec.spec_version,
            "experiment_type": "obstacle_flow_2d",
            "flow_mode": spec.flow_definition.mode.value,
            "has_cylinder": spec.has_cylinder,
            "has_bump": spec.has_bump,
            "domain": {
                "length_m": spec.domain.length_m,
                "height_m": spec.domain.height_m,
                "thickness_m": spec.domain.thickness_m,
            },
            "fluid": {
                "type": spec.fluid.type,
                "density_kg_m3": spec.fluid.density_kg_m3,
                "kinematic_viscosity_m2_s": spec.fluid.kinematic_viscosity_m2_s,
            },
            "compiler_id": self.compiler_id,
            "compiler_version": self.compiler_version,
            "reynolds_estimate": spec.estimate_reynolds(),
        }
        files["fluidScientist/spec.json"] = json.dumps(
            metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )

        # Create archive
        normalized = {name: _normalize(text) for name, text in files.items()}
        archive = _deterministic_tar_gz(normalized)
        archive_sha = "sha256:" + hashlib.sha256(archive).hexdigest()

        compiled = ObstacleFlowCompiledCase(
            files=files,
            archive=archive,
            archive_sha256=archive_sha,
            spec_version=spec.spec_version,
            preprocessing=self._determine_preprocessing(spec),
            required_outputs=self._determine_required_outputs(spec),
        )

        spec_hash = hashlib.sha256(
            spec.model_dump_json().encode()
        ).hexdigest()[:16]
        case_hash = hashlib.sha256(archive).hexdigest()[:16]

        import uuid
        manifest = CompilationManifest(
            compilation_id=f"comp-{uuid.uuid4().hex[:16]}",
            spec_version=spec.spec_version,
            spec_hash=spec_hash,
            case_hash=case_hash,
            generated_files=sorted(files.keys()),
            compiler_id=self.compiler_id,
            compiler_version=self.compiler_version,
            flow_mode=spec.flow_definition.mode.value,
            has_cylinder=spec.has_cylinder,
            has_bump=spec.has_bump,
        )

        return compiled, manifest

    # --- Pre-compilation validation ---

    def _validate_material_consistency(self, spec: ObstacleFlowExperimentSpecV1) -> None:
        """Check that the declared fluid type is consistent with density and viscosity.

        Raises ``CompilationError`` when the fluid type implies a known
        material (water, air) but the supplied density or kinematic
        viscosity falls far outside the expected physical range for that
        material.  This catches dependencies where, for example, air
        density is paired with water viscosity.
        """
        fluid = spec.fluid
        ftype = (fluid.type or "").lower().strip()
        rho = fluid.density_kg_m3
        nu = fluid.kinematic_viscosity_m2_s

        # Known fluid property ranges at standard conditions.
        # These are PHYSICAL ranges — we do NOT widen them to accommodate
        # Re-derived viscosities.  When the user specifies Re=200 with
        # water at U=1m/s, D=0.2m, the derived nu=0.001 is NOT real water
        # (real water nu ≈ 1e-6).  This is a physical inconsistency that
        # must be surfaced to the user, not silently accepted.
        KNOWN_FLUIDS = {
            "water": {
                "rho_min": 950.0, "rho_max": 1050.0,
                "nu_min": 5e-7, "nu_max": 2e-5,
            },
            "air": {
                "rho_min": 0.8, "rho_max": 1.5,
                "nu_min": 1e-6, "nu_max": 5e-5,
            },
        }

        if ftype in KNOWN_FLUIDS:
            bounds = KNOWN_FLUIDS[ftype]
            if rho < bounds["rho_min"] or rho > bounds["rho_max"]:
                raise CompilationError(
                    f"Fluid type '{fluid.type}' has density {rho} kg/m3 which "
                    f"is outside the physical range "
                    f"[{bounds['rho_min']}, {bounds['rho_max']}] for {ftype}. "
                    f"Material dependency violation. If you need a non-standard "
                    f"fluid, set fluid type to 'custom'."
                )
            if nu < bounds["nu_min"] or nu > bounds["nu_max"]:
                raise CompilationError(
                    f"Fluid type '{fluid.type}' has kinematic viscosity {nu} "
                    f"m2/s which is outside the physical range "
                    f"[{bounds['nu_min']}, {bounds['nu_max']}] for {ftype}. "
                    f"This usually means Re was specified with incompatible "
                    f"fluid properties (e.g. Re=200 with water gives "
                    f"nu=U*D/Re=0.001, but real water nu≈1e-6). "
                    f"Options: (1) keep real water properties and let Re be "
                    f"computed from U, D, nu; (2) set fluid type to 'custom' "
                    f"for a high-viscosity fluid; (3) adjust U or D to match "
                    f"the desired Re with real water."
                )

    def _validate_rotating_cylinder(self, spec: ObstacleFlowExperimentSpecV1) -> None:
        """Validate that a rotating-wall cylinder has a non-zero angular velocity.

        A ``ROTATING_WALL`` boundary with ``angular_velocity_rad_s == 0``
        is a capability error — the user requested rotation but no
        rotation speed was supplied, which would silently produce a
        stationary wall.
        """
        for cyl in spec.cylinders:
            if cyl.boundary_type == CylinderBoundaryType.ROTATING_WALL:
                if cyl.angular_velocity_rad_s == 0:
                    raise CompilationError(
                        f"Cylinder '{cyl.id}' has ROTATING_WALL boundary type "
                        f"but angular_velocity_rad_s is 0 — a rotating cylinder "
                        f"must have a non-zero angular velocity."
                    )

    def _validate_observable_geometry(self, spec: ObstacleFlowExperimentSpecV1) -> None:
        """Validate that observables are consistent with the geometry.

        Raises ``CompilationError`` when a cylinder-specific observable
        (CYLINDER_DRAG / CYLINDER_LIFT) is requested but no cylinder is
        present in the spec.
        """
        has_force_obs = any(
            o.type in (ObservableType.CYLINDER_DRAG, ObservableType.CYLINDER_LIFT)
            for o in spec.observables
        )
        if has_force_obs and not spec.has_cylinder:
            raise CompilationError(
                "CYLINDER_DRAG / CYLINDER_LIFT observables are requested but "
                "no cylinder is present in the spec — force measurement "
                "requires a cylinder obstacle."
            )

    # --- Field files ---

    def _compile_velocity_field(self, spec: ObstacleFlowExperimentSpecV1) -> str:
        """Generate 0/U — velocity initial and boundary conditions."""
        b = spec.boundaries
        init = spec.flow_definition.initial_velocity

        # Initial value
        if init.type == "quiescent":
            internal = "uniform (0 0 0)"
        else:
            v = init.vector_m_s
            internal = f"uniform ({_fmt(v[0])} {_fmt(v[1])} {_fmt(v[2] if len(v) > 2 else 0)})"

        lines = [
            _header("volVectorField", "U"),
            f"dimensions      [0 1 -1 0 0 0 0];",
            f"internalField   {internal};",
            "boundaryField",
            "{",
        ]

        # Left boundary
        lines.append("    left")
        lines.append("    {")
        lines.append(f"        {self._velocity_bc(spec, b.left, 'left')}")
        lines.append("    }")

        # Right boundary
        lines.append("    right")
        lines.append("    {")
        lines.append(f"        {self._velocity_bc(spec, b.right, 'right')}")
        lines.append("    }")

        # Top boundary
        lines.append("    top")
        lines.append("    {")
        lines.append(f"        {self._velocity_bc(spec, b.top, 'top')}")
        lines.append("    }")

        # Bottom
        lines.append("    bottom")
        lines.append("    {")
        lines.append(f"        {self._velocity_bc(spec, b.bottom_flat, 'bottom')}")
        lines.append("    }")

        # Cylinder (if present)
        if spec.has_cylinder:
            lines.append("    cylinder")
            lines.append("    {")
            cyl = spec.cylinders[0]
            if cyl.boundary_type == CylinderBoundaryType.NO_SLIP_WALL:
                lines.append("        type            noSlip;")
            elif cyl.boundary_type == CylinderBoundaryType.SLIP_WALL:
                lines.append("        type            slip;")
            elif cyl.boundary_type == CylinderBoundaryType.ROTATING_WALL:
                omega = cyl.angular_velocity_rad_s
                direction = 1.0 if cyl.rotation_direction == "ccw" else -1.0
                cx = cyl.center_x_m or 0.0
                cy = cyl.center_y_m or 0.0
                lines.append("        type            rotatingWallVelocity;")
                lines.append(f"        origin          ({_fmt(cx)} {_fmt(cy)} 0);")
                lines.append(f"        axis            (0 0 1);")
                lines.append(f"        omega           {_fmt(omega * direction)};")
            lines.append("    }")

        # Rectangle (if present)
        if spec.has_rectangle:
            lines.append("    rectangle")
            lines.append("    {")
            lines.append("        type            noSlip;")
            lines.append("    }")

        # Triangle (if present)
        if spec.has_triangle:
            lines.append("    triangle")
            lines.append("    {")
            lines.append("        type            noSlip;")
            lines.append("    }")

        # Trapezoid (if present)
        if spec.has_trapezoid:
            lines.append("    trapezoid")
            lines.append("    {")
            lines.append("        type            noSlip;")
            lines.append("    }")

        # Polygon (if present)
        if spec.has_polygon:
            lines.append("    polygon")
            lines.append("    {")
            lines.append("        type            noSlip;")
            lines.append("    }")

        # frontAndBack
        lines.append("    frontAndBack")
        lines.append("    {")
        lines.append("        type            empty;")
        lines.append("    }")

        lines.append("}")
        lines.append("")
        lines.append("// ************************************************************************* //")

        return "\n".join(lines)

    def _velocity_bc(
        self,
        spec: ObstacleFlowExperimentSpecV1,
        boundary,
        side: str,
    ) -> str:
        """Generate velocity boundary condition for a specific side."""
        bt = boundary.type

        if bt == BoundaryType.VELOCITY_INLET:
            # Time-varying inlet: emit uniformFixedValue with a tableFile
            # Function1 so sinusoidal / ramp / tabulated profiles are
            # honoured by the solver at runtime.
            if self._is_time_varying_inlet(spec):
                return (
                    "type            uniformFixedValue;\n"
                    "        uniformValue\n"
                    "        {\n"
                    "            type            tableFile;\n"
                    "            file            \"constant/inletVelocity.table\";\n"
                    "            format          openfoam;\n"
                    "            outOfBounds     clamp;\n"
                    "        }"
                )
            v = boundary.inlet_velocity or 0.0
            return f"type            fixedValue;\n        value           uniform ({_fmt(v)} 0 0);"
        elif bt == BoundaryType.PRESSURE_INLET:
            return "type            pressureInletOutletVelocity;\n        value           uniform (0 0 0);"
        elif bt == BoundaryType.MASS_FLOW_INLET:
            v = boundary.inlet_velocity or 0.0
            return f"type            flowRateInletVelocity;\n        massFlowRate    {_fmt(v)};\n        value           uniform (0 0 0);"
        elif bt == BoundaryType.PRESSURE_OUTLET:
            return "type            inletOutlet;\n        inletValue      uniform (0 0 0);\n        value           uniform (0 0 0);"
        elif bt == BoundaryType.OPEN_OUTLET:
            return "type            inletOutlet;\n        inletValue      uniform (0 0 0);\n        value           uniform (0 0 0);"
        elif bt == BoundaryType.ADVECTIVE_OUTLET:
            return "type            advective;\n        phi             phi;"
        elif bt == BoundaryType.NO_SLIP_WALL:
            return "type            noSlip;"
        elif bt == BoundaryType.SLIP_WALL:
            return "type            slip;"
        elif bt == BoundaryType.MOVING_WALL:
            v = boundary.velocity_vector or [0, 0, 0]
            return f"type            fixedValue;\n        value           uniform ({_fmt(v[0])} {_fmt(v[1])} {_fmt(v[2] if len(v) > 2 else 0)});"
        elif bt == BoundaryType.SHEAR_STRESS:
            return "type            slip;"  # Shear stress handled via fvModels
        elif bt == BoundaryType.SYMMETRY:
            return "type            symmetry;"
        elif bt == BoundaryType.FREESTREAM:
            v = boundary.freestream_velocity or 0.0
            return f"type            freestreamVelocity;\n        freestreamValue uniform ({_fmt(v)} 0 0);"
        elif bt == BoundaryType.OPEN_BOUNDARY:
            return "type            inletOutlet;\n        inletValue      uniform (0 0 0);\n        value           uniform (0 0 0);"
        elif bt == BoundaryType.PERIODIC:
            return "type            cyclic;"
        elif bt == BoundaryType.PRESSURE_BOUNDARY:
            return "type            inletOutlet;\n        inletValue      uniform (0 0 0);\n        value           uniform (0 0 0);"
        else:
            return "type            zeroGradient;"

    # --- Time-varying inlet support ---

    def _is_time_varying_inlet(self, spec: ObstacleFlowExperimentSpecV1) -> bool:
        """Return True when the spec has a non-constant temporal inlet profile."""
        ip = spec.inlet_profile
        return ip.enabled and ip.temporal_type != TemporalType.CONSTANT

    def _compile_inlet_velocity_table(
        self, spec: ObstacleFlowExperimentSpecV1
    ) -> str | None:
        """Generate the inlet velocity time-series table file.

        Produces an OpenFOAM ``tableFile`` (openfoam format) with
        ``(time (vx vy vz))`` entries covering the full simulation
        duration.  Returns ``None`` when the inlet profile is constant or
        disabled, in which case no table file is emitted and the inlet
        falls back to a plain ``fixedValue`` BC.
        """
        ip = spec.inlet_profile
        if not ip.enabled or ip.temporal_type == TemporalType.CONSTANT:
            return None

        p = ip.parameters
        end_time, _, _ = self.compute_time_step(spec)
        end_time = max(float(end_time), 1.0)  # guard against zero/negative

        rows: list[tuple[float, list[float]]] = []

        if ip.temporal_type == TemporalType.SINUSOIDAL:
            mean = float(p.get("mean_velocity", 0.0))
            amp = float(p.get("amplitude", 0.0))
            freq = float(p.get("frequency", 0.0))
            phase = float(p.get("phase", 0.0))
            # Resolution: 24 points per period, at least 100, capped at 2000
            period = (1.0 / freq) if freq > 0 else end_time
            n_periods = end_time / period if period > 0 else 1.0
            n_points = max(100, int(math.ceil(n_periods * 24)))
            n_points = min(n_points, 2000)
            for i in range(n_points + 1):
                t = end_time * i / n_points
                v = mean + amp * math.sin(2.0 * math.pi * freq * t + phase)
                rows.append((t, [v, 0.0, 0.0]))

        elif ip.temporal_type == TemporalType.RAMP:
            v0 = float(p.get("start_velocity", 0.0))
            v1 = float(p.get("end_velocity", v0))
            t0 = float(p.get("start_time", 0.0))
            # 'end_time' key inside parameters is the *ramp* end time
            t1 = float(p.get("end_time", end_time))
            t1 = min(t1, end_time)
            # Before the ramp
            if t0 > 0:
                rows.append((0.0, [v0, 0.0, 0.0]))
                rows.append((t0, [v0, 0.0, 0.0]))
            else:
                rows.append((0.0, [v0, 0.0, 0.0]))
            # During the ramp — 50 intermediate points
            n_ramp = 50
            span = (t1 - t0) if t1 > t0 else 1.0
            for i in range(1, n_ramp + 1):
                t = t0 + span * i / n_ramp
                v = v0 + (v1 - v0) * (i / n_ramp)
                rows.append((t, [v, 0.0, 0.0]))
            # Hold the final value until the simulation end
            if t1 < end_time:
                rows.append((end_time, [v1, 0.0, 0.0]))

        elif ip.temporal_type in (TemporalType.PIECEWISE_LINEAR, TemporalType.TABULATED):
            key = "points" if ip.temporal_type == TemporalType.PIECEWISE_LINEAR else "data"
            pts = p.get(key, [])
            if not pts:
                return None
            # Prepend t=0 with the first value if the table starts later
            first_t = float(pts[0][0])
            first_v = float(pts[0][1])
            if first_t > 0:
                rows.append((0.0, [first_v, 0.0, 0.0]))
            for pt in pts:
                rows.append((float(pt[0]), [float(pt[1]), 0.0, 0.0]))
            # Extend the last value to the simulation end
            last_t = float(pts[-1][0])
            last_v = float(pts[-1][1])
            if last_t < end_time:
                rows.append((end_time, [last_v, 0.0, 0.0]))

        else:
            return None

        lines = ["("]
        for t, vec in rows:
            lines.append(
                f"    ({_fmt(t)} ({_fmt(vec[0])} {_fmt(vec[1])} {_fmt(vec[2])}))"
            )
        lines.append(")")
        lines.append("")
        return "\n".join(lines)

    def _compile_pressure_field(self, spec: ObstacleFlowExperimentSpecV1) -> str:
        """Generate 0/p — pressure initial and boundary conditions."""
        b = spec.boundaries

        lines = [
            _header("volScalarField", "p"),
            "dimensions      [0 2 -2 0 0 0 0];",
            "internalField   uniform 0;",
            "boundaryField",
            "{",
        ]

        # Left
        lines.append("    left")
        lines.append("    {")
        lines.append(f"        {self._pressure_bc(b.left, 'left')}")
        lines.append("    }")

        # Right
        lines.append("    right")
        lines.append("    {")
        lines.append(f"        {self._pressure_bc(b.right, 'right')}")
        lines.append("    }")

        # Top
        lines.append("    top")
        lines.append("    {")
        lines.append(f"        {self._pressure_bc(b.top, 'top')}")
        lines.append("    }")

        # Bottom
        lines.append("    bottom")
        lines.append("    {")
        lines.append(f"        {self._pressure_bc(b.bottom_flat, 'bottom')}")
        lines.append("    }")

        # Cylinder
        if spec.has_cylinder:
            lines.append("    cylinder")
            lines.append("    {")
            lines.append("        type            zeroGradient;")
            lines.append("    }")

        # Rectangle
        if spec.has_rectangle:
            lines.append("    rectangle")
            lines.append("    {")
            lines.append("        type            zeroGradient;")
            lines.append("    }")

        # Triangle
        if spec.has_triangle:
            lines.append("    triangle")
            lines.append("    {")
            lines.append("        type            zeroGradient;")
            lines.append("    }")

        # Trapezoid
        if spec.has_trapezoid:
            lines.append("    trapezoid")
            lines.append("    {")
            lines.append("        type            zeroGradient;")
            lines.append("    }")

        # Polygon
        if spec.has_polygon:
            lines.append("    polygon")
            lines.append("    {")
            lines.append("        type            zeroGradient;")
            lines.append("    }")

        # frontAndBack
        lines.append("    frontAndBack")
        lines.append("    {")
        lines.append("        type            empty;")
        lines.append("    }")

        lines.append("}")
        lines.append("")
        lines.append("// ************************************************************************* //")

        return "\n".join(lines)

    def _pressure_bc(self, boundary, side: str) -> str:
        """Generate pressure boundary condition."""
        bt = boundary.type

        if bt == BoundaryType.VELOCITY_INLET:
            return "type            zeroGradient;"
        elif bt == BoundaryType.MASS_FLOW_INLET:
            return "type            zeroGradient;"
        elif bt == BoundaryType.PRESSURE_INLET:
            p = boundary.pressure_value or 0.0
            return f"type            fixedValue;\n        value           uniform {_fmt(p)};"
        elif bt == BoundaryType.PRESSURE_OUTLET:
            p = boundary.pressure_value or 0.0
            return f"type            fixedValue;\n        value           uniform {_fmt(p)};"
        elif bt in (BoundaryType.OPEN_OUTLET, BoundaryType.ADVECTIVE_OUTLET):
            return "type            totalPressure;\n        p0              uniform 0;"
        elif bt == BoundaryType.NO_SLIP_WALL:
            return "type            zeroGradient;"
        elif bt == BoundaryType.SLIP_WALL:
            return "type            zeroGradient;"
        elif bt == BoundaryType.MOVING_WALL:
            return "type            zeroGradient;"
        elif bt == BoundaryType.SHEAR_STRESS:
            return "type            zeroGradient;"
        elif bt == BoundaryType.SYMMETRY:
            return "type            symmetry;"
        elif bt == BoundaryType.FREESTREAM:
            return "type            freestreamPressure;\n        freestreamValue uniform 0;"
        elif bt == BoundaryType.OPEN_BOUNDARY:
            return "type            totalPressure;\n        p0              uniform 0;"
        elif bt == BoundaryType.PERIODIC:
            return "type            cyclic;"
        elif bt == BoundaryType.PRESSURE_BOUNDARY:
            p = boundary.pressure_value or 0.0
            return f"type            fixedValue;\n        value           uniform {_fmt(p)};"
        else:
            return "type            zeroGradient;"

    def _compile_tke_field(self, spec: ObstacleFlowExperimentSpecV1) -> str:
        """Generate 0/k — turbulent kinetic energy."""
        # Estimate k from Reynolds number and velocity
        re = spec.estimate_reynolds() or 1000
        nu = spec.fluid.kinematic_viscosity_m2_s
        char_len = spec.domain.height_m
        if spec.has_cylinder and spec.cylinders[0].diameter_m is not None:
            char_len = spec.cylinders[0].diameter_m
        u = re * nu / char_len if char_len > 0 else 1.0

        # k = 1.5 * (I * U)^2, I = 5% for medium turbulence
        I = 0.05
        k_val = 1.5 * (I * u) ** 2

        lines = [
            _header("volScalarField", "k"),
            "dimensions      [0 2 -2 0 0 0 0];",
            f"internalField   uniform {_fmt(k_val)};",
            "boundaryField",
            "{",
        ]
        # Simplified: all walls zeroGradient, inlet fixedValue, outlet zeroGradient
        for side in ("left", "right", "top", "bottom"):
            lines.append(f"    {side}")
            lines.append("    {")
            if side == "left" and spec.boundaries.left.type == BoundaryType.VELOCITY_INLET:
                lines.append(f"        type            fixedValue;\n        value           uniform {_fmt(k_val)};")
            elif side == "right":
                lines.append("        type            inletOutlet;\n        inletValue      uniform 0;")
            else:
                lines.append("        type            kqRWallFunction;\n        value           uniform 0;")
            lines.append("    }")
        if spec.has_cylinder:
            lines.append("    cylinder")
            lines.append("    {")
            lines.append("        type            kqRWallFunction;\n        value           uniform 0;")
            lines.append("    }")
        if spec.has_rectangle:
            lines.append("    rectangle")
            lines.append("    {")
            lines.append("        type            kqRWallFunction;\n        value           uniform 0;")
            lines.append("    }")
        if spec.has_triangle:
            lines.append("    triangle")
            lines.append("    {")
            lines.append("        type            kqRWallFunction;\n        value           uniform 0;")
            lines.append("    }")
        if spec.has_trapezoid:
            lines.append("    trapezoid")
            lines.append("    {")
            lines.append("        type            kqRWallFunction;\n        value           uniform 0;")
            lines.append("    }")
        if spec.has_polygon:
            lines.append("    polygon")
            lines.append("    {")
            lines.append("        type            kqRWallFunction;\n        value           uniform 0;")
            lines.append("    }")
        lines.append("    frontAndBack")
        lines.append("    {")
        lines.append("        type            empty;")
        lines.append("    }")
        lines.append("}")
        lines.append("")
        lines.append("// ************************************************************************* //")
        return "\n".join(lines)

    def _compile_omega_field(self, spec: ObstacleFlowExperimentSpecV1) -> str:
        """Generate 0/omega — specific dissipation rate."""
        re = spec.estimate_reynolds() or 1000
        nu = spec.fluid.kinematic_viscosity_m2_s
        char_len = spec.domain.height_m
        if spec.has_cylinder and spec.cylinders[0].diameter_m is not None:
            char_len = spec.cylinders[0].diameter_m
        u = re * nu / char_len if char_len > 0 else 1.0
        I = 0.05
        k_val = 1.5 * (I * u) ** 2
        # omega = k / (C_mu^0.5 * l), C_mu = 0.09
        l = 0.07 * char_len  # Turbulent length scale
        omega_val = k_val / (math.sqrt(0.09) * l) if l > 0 else 1.0

        lines = [
            _header("volScalarField", "omega"),
            "dimensions      [0 0 -1 0 0 0 0];",
            f"internalField   uniform {_fmt(omega_val)};",
            "boundaryField",
            "{",
        ]
        for side in ("left", "right", "top", "bottom"):
            lines.append(f"    {side}")
            lines.append("    {")
            if side == "left" and spec.boundaries.left.type == BoundaryType.VELOCITY_INLET:
                lines.append(f"        type            fixedValue;\n        value           uniform {_fmt(omega_val)};")
            elif side == "right":
                lines.append("        type            inletOutlet;\n        inletValue      uniform 0;")
            else:
                lines.append("        type            omegaWallFunction;\n        value           uniform 0;")
            lines.append("    }")
        if spec.has_cylinder:
            lines.append("    cylinder")
            lines.append("    {")
            lines.append("        type            omegaWallFunction;\n        value           uniform 0;")
            lines.append("    }")
        if spec.has_rectangle:
            lines.append("    rectangle")
            lines.append("    {")
            lines.append("        type            omegaWallFunction;\n        value           uniform 0;")
            lines.append("    }")
        if spec.has_triangle:
            lines.append("    triangle")
            lines.append("    {")
            lines.append("        type            omegaWallFunction;\n        value           uniform 0;")
            lines.append("    }")
        if spec.has_trapezoid:
            lines.append("    trapezoid")
            lines.append("    {")
            lines.append("        type            omegaWallFunction;\n        value           uniform 0;")
            lines.append("    }")
        if spec.has_polygon:
            lines.append("    polygon")
            lines.append("    {")
            lines.append("        type            omegaWallFunction;\n        value           uniform 0;")
            lines.append("    }")
        lines.append("    frontAndBack")
        lines.append("    {")
        lines.append("        type            empty;")
        lines.append("    }")
        lines.append("}")
        lines.append("")
        lines.append("// ************************************************************************* //")
        return "\n".join(lines)

    def _compile_nut_field(self, spec: ObstacleFlowExperimentSpecV1) -> str:
        """Generate 0/nut — turbulent viscosity."""
        nu = spec.fluid.kinematic_viscosity_m2_s
        nut_val = 0.1 * nu  # 10% of laminar viscosity

        lines = [
            _header("volScalarField", "nut"),
            "dimensions      [0 2 -1 0 0 0 0];",
            f"internalField   uniform {_fmt(nut_val)};",
            "boundaryField",
            "{",
        ]
        for side in ("left", "right", "top", "bottom"):
            lines.append(f"    {side}")
            lines.append("    {")
            if side in ("top", "bottom"):
                lines.append("        type            nutkWallFunction;\n        value           uniform 0;")
            else:
                lines.append("        type            calculated;\n        value           uniform 0;")
            lines.append("    }")
        if spec.has_cylinder:
            lines.append("    cylinder")
            lines.append("    {")
            lines.append("        type            nutkWallFunction;\n        value           uniform 0;")
            lines.append("    }")
        if spec.has_rectangle:
            lines.append("    rectangle")
            lines.append("    {")
            lines.append("        type            nutkWallFunction;\n        value           uniform 0;")
            lines.append("    }")
        if spec.has_triangle:
            lines.append("    triangle")
            lines.append("    {")
            lines.append("        type            nutkWallFunction;\n        value           uniform 0;")
            lines.append("    }")
        if spec.has_trapezoid:
            lines.append("    trapezoid")
            lines.append("    {")
            lines.append("        type            nutkWallFunction;\n        value           uniform 0;")
            lines.append("    }")
        if spec.has_polygon:
            lines.append("    polygon")
            lines.append("    {")
            lines.append("        type            nutkWallFunction;\n        value           uniform 0;")
            lines.append("    }")
        lines.append("    frontAndBack")
        lines.append("    {")
        lines.append("        type            empty;")
        lines.append("    }")
        lines.append("}")
        lines.append("")
        lines.append("// ************************************************************************* //")
        return "\n".join(lines)

    # --- Constant files ---

    def _compile_physical_properties(self, spec: ObstacleFlowExperimentSpecV1) -> str:
        """Generate constant/physicalProperties for OpenFOAM Foundation 13.

        Foundation 13 incompressibleFluid requires viscosityModel keyword.
        """
        nu = spec.fluid.kinematic_viscosity_m2_s
        rho = spec.fluid.density_kg_m3

        lines = [
            _header("dictionary", "physicalProperties"),
            "viscosityModel  Newtonian;",
            "",
            "nu              [0 2 -1 0 0 0 0] " + _fmt(nu) + ";",
            "rho             [1 -3 0 0 0 0 0] " + _fmt(rho) + ";",
            "",
            "// ************************************************************************* //",
        ]
        return "\n".join(lines)

    def _compile_momentum_transport(self, spec: ObstacleFlowExperimentSpecV1) -> str:
        """Generate constant/momentumTransport."""
        if spec.is_turbulent:
            model = "RAS"
            lines = [
                _header("dictionary", "momentumTransport"),
                "simulationType  RAS;",
                "",
                "RAS",
                "{",
                "    model           kOmegaSST;",
                "}",
                "",
                "// ************************************************************************* //",
            ]
        else:
            lines = [
                _header("dictionary", "momentumTransport"),
                "simulationType  laminar;",
                "",
                "// ************************************************************************* //",
            ]
        return "\n".join(lines)

    def _compile_fv_models(self, spec: ObstacleFlowExperimentSpecV1) -> str | None:
        """Generate system/fvModels for pressure gradient or body force."""
        has_pg = spec.forcing.pressure_gradient.enabled
        has_bf = spec.forcing.body_force.enabled

        if not has_pg and not has_bf:
            return None

        lines = [
            _header("dictionary", "fvModels"),
            "",
        ]

        if has_pg:
            body_force = spec.equivalent_body_force()
            if body_force is not None:
                lines.append("pressureGradient")
                lines.append("{")
                lines.append("    type            bodyForce;")
                lines.append(f"    selectionMode   all;")
                lines.append(f"    force           ({_fmt(body_force[0])} {_fmt(body_force[1])} {_fmt(body_force[2])});")
                lines.append("}")
                lines.append("")

        if has_bf:
            bf = spec.forcing.body_force.vector_m_s2
            lines.append("bodyForce")
            lines.append("{")
            lines.append("    type            bodyForce;")
            lines.append("    selectionMode   all;")
            lines.append(f"    force           ({_fmt(bf[0])} {_fmt(bf[1])} {_fmt(bf[2])});")
            lines.append("}")
            lines.append("")

        lines.append("// ************************************************************************* //")
        return "\n".join(lines)

    # --- System files ---

    def compute_time_step(
        self, spec: ObstacleFlowExperimentSpecV1
    ) -> tuple[float, float, int]:
        """Compute (end_time, delta_t, write_interval) for the given spec.

        This is the single source of truth for time-step estimation.
        Both :meth:`_compile_control_dict` (which writes the actual
        ``controlDict``) and external preview code (e.g.
        ``_generate_compile_preview`` in the cylinder-flow router) call
        this method so that the preview shown to the user always matches
        the value that ends up in the compiled ``controlDict``.

        For steady-state simulations *end_time* represents the iteration
        count rather than physical time; the user-specified value is
        honoured, falling back to 1000 when unset.
        """
        sim = spec.simulation
        is_transient = spec.is_transient

        # Validate delta_t is positive when explicitly set
        if sim.delta_t is not None and sim.delta_t <= 0:
            raise CompilationError(
                f"Simulation delta_t must be positive, got {sim.delta_t} — "
                f"a non-positive time step is physically invalid."
            )

        if is_transient:
            end_time = sim.end_time or 100.0
            if sim.delta_t is not None:
                delta_t = sim.delta_t
            else:
                # Estimate delta_t from Courant number
                # Account for snappyHexMesh refinement near cylinder (3-4 levels)
                re = spec.estimate_reynolds() or 100
                nu = spec.fluid.kinematic_viscosity_m2_s
                char_len = spec.domain.height_m
                if spec.has_cylinder and spec.cylinders[0].diameter_m is not None:
                    char_len = spec.cylinders[0].diameter_m
                u = re * nu / char_len if char_len > 0 else 1.0
                # Use 1/200 of char_len to account for mesh refinement
                cell_size = char_len / 200
                delta_t = sim.max_courant_number * cell_size / max(u, 0.001)
                delta_t = min(delta_t, end_time / 200)
            write_interval = sim.write_interval or max(1, int(end_time / (delta_t * 20)))
        else:
            # Steady-state: end_time is the iteration count.
            # Honour the user-specified value, fall back to 1000.
            end_time = sim.end_time if sim.end_time is not None else 1000.0
            delta_t = 1.0
            write_interval = sim.write_interval or 100

        return end_time, delta_t, write_interval

    def _compile_control_dict(self, spec: ObstacleFlowExperimentSpecV1) -> str:
        """Generate system/controlDict."""
        is_transient = spec.is_transient
        end_time, delta_t, write_interval = self.compute_time_step(spec)

        lines = [
            _header("dictionary", "controlDict"),
            "solver          incompressibleFluid;",
            "",
            "libs            (\"libforces.so\");",
            "",
        ]

        if is_transient:
            lines.append(f"startFrom       startTime;")
            lines.append(f"startTime       0;")
            lines.append(f"stopAt          endTime;")
            lines.append(f"endTime         {_fmt(end_time)};")
            lines.append(f"deltaT          {_fmt(delta_t)};")
            lines.append(f"writeControl    runTime;")
            lines.append(f"writeInterval   {_fmt(max(delta_t * write_interval, end_time / 20))};")
            lines.append(f"purgeWrite      0;")
            lines.append(f"adjustTimeStep  no;")
        else:
            lines.append(f"startFrom       startTime;")
            lines.append(f"startTime       0;")
            lines.append(f"stopAt          endTime;")
            lines.append(f"endTime         {_fmt(end_time)};")
            lines.append(f"deltaT          1;")
            lines.append(f"writeControl    timeStep;")
            lines.append(f"writeInterval   {write_interval};")
            lines.append(f"purgeWrite      3;")

        lines.extend([
            "",
            "functions",
            "{",
        ])

        # Add functionObjects based on observables
        fo_lines = self._compile_function_objects(spec)
        lines.extend(fo_lines)

        lines.append("}")
        lines.append("")
        lines.append("// ************************************************************************* //")

        return "\n".join(lines)

    def _compile_function_objects(self, spec: ObstacleFlowExperimentSpecV1) -> list[str]:
        """Generate functionObjects based on observables and plot requests."""
        lines: list[str] = []

        # Force coefficients for cylinder
        has_force = any(
            o.type in (ObservableType.CYLINDER_DRAG, ObservableType.CYLINDER_LIFT)
            for o in spec.observables
        )
        if has_force and spec.has_cylinder:
            cyl = spec.cylinders[0]
            d = cyl.diameter_m or 1.0
            rho = spec.fluid.density_kg_m3
            nu = spec.fluid.kinematic_viscosity_m2_s
            re = spec.estimate_reynolds() or 100
            u = re * nu / d
            area = d * spec.domain.thickness_m  # 2D frontal area

            lines.append("    forceCoeffs1")
            lines.append("    {")
            lines.append("        type            forceCoeffs;")
            lines.append("        libs            (\"libforces.so\");")
            patches = ["cylinder"]
            if spec.has_rectangle:
                patches.append("rectangle")
            if spec.has_triangle:
                patches.append("triangle")
            if spec.has_trapezoid:
                patches.append("trapezoid")
            if spec.has_polygon:
                patches.append("polygon")
            lines.append(f"        patches         ({' '.join(patches)});")
            lines.append("        rho             rhoInf;")
            lines.append(f"        rhoInf          {_fmt(rho)};")
            lines.append(f"        magUInf         {_fmt(u)};")
            lines.append(f"        lRef            {_fmt(d)};")
            lines.append(f"        Aref            {_fmt(area)};")
            lines.append("        dragDir         (1 0 0);")
            lines.append("        liftDir         (0 1 0);")
            cx = cyl.center_x_m or 0.0
            cy = cyl.center_y_m or 0.0
            lines.append(f"        CofR            ({_fmt(cx)} {_fmt(cy)} 0);")
            lines.append("        pitchAxis       (0 1 0);")
            lines.append("        writeControl    timeStep;")
            lines.append("        writeInterval   1;")
            lines.append("    }")
            lines.append("")

        # Probes for point velocity and section mean velocity
        probe_points: list[list[float]] = []
        for obs in spec.observables:
            if obs.type == ObservableType.POINT_VELOCITY and obs.point is not None:
                probe_points.append(list(obs.point))
            elif (
                obs.type == ObservableType.SECTION_MEAN_VELOCITY
                and obs.section_x is not None
            ):
                # Distribute probe points vertically along the section so
                # the mean velocity can be reconstructed from the
                # resulting time-series.
                n_section_probes = 10
                h = spec.domain.height_m
                for i in range(n_section_probes + 1):
                    y = h * i / n_section_probes
                    probe_points.append([obs.section_x, y, 0.0])

        if probe_points:
            lines.append("    probes1")
            lines.append("    {")
            lines.append("        type            probes;")
            lines.append("        libs            (\"libsampling.so\");")
            lines.append("        writeControl    timeStep;")
            lines.append("        writeInterval   1;")
            lines.append("        fields          (U);")
            lines.append("        probeLocations")
            lines.append("        (")
            for p in probe_points:
                z = p[2] if len(p) > 2 else 0.0
                lines.append(f"            ({_fmt(p[0])} {_fmt(p[1])} {_fmt(z)})")
            lines.append("        );")
            lines.append("    }")
            lines.append("")

        # Surface sampling for section mean velocity
        section_obs = [
            o for o in spec.observables
            if o.type in (ObservableType.SECTION_MEAN_VELOCITY, ObservableType.SECTION_FLOW_RATE)
        ]
        if section_obs:
            lines.append("    surfaces1")
            lines.append("    {")
            lines.append("        type            surfaces;")
            lines.append("        libs            (\"libsampling.so\");")
            lines.append("        writeControl    timeStep;")
            lines.append("        writeInterval   10;")
            lines.append("        surfaceFormat   raw;")
            lines.append("        interpolationScheme cellPoint;")
            lines.append("        fields          (U);")
            lines.append("        surfaces")
            lines.append("        (")
            for obs in section_obs:
                if obs.section_x is not None:
                    lines.append(f"            x{_int_str(obs.section_x)}")
                    lines.append("            {")
                    lines.append("                type            cuttingPlane;")
                    lines.append("                planeType       pointAndNormal;")
                    lines.append(f"                point           ({_fmt(obs.section_x)} 0 0);")
                    lines.append("                normal          (1 0 0);")
                    lines.append("                interpolate     true;")
                    lines.append("            }")
            lines.append("        );")
            lines.append("    }")
            lines.append("")

        # Wall shear stress
        wss_obs = [
            o for o in spec.observables if o.type == ObservableType.WALL_SHEAR_STRESS
        ]
        if wss_obs:
            available_walls = self._available_wall_patches(spec)
            wall_names: list[str] = []
            for obs in wss_obs:
                wname = obs.wall_name
                if wname and wname not in wall_names and wname in available_walls:
                    wall_names.append(wname)
            # Fallback: if no recognised wall names, probe all wall patches
            if not wall_names:
                wall_names = list(available_walls)

            lines.append("    wallShearStress1")
            lines.append("    {")
            lines.append("        type            wallShearStress;")
            lines.append("        libs            (\"libforces.so\");")
            lines.append(f"        patches         ({' '.join(wall_names)});")
            lines.append("        writeControl    writeTime;")
            lines.append("    }")
            lines.append("")

        # Residuals
        lines.append("    residuals1")
        lines.append("    {")
        lines.append("        type            residuals;")
        lines.append("        libs            (\"libutilityFunctionObjects.so\");")
        lines.append("        fields          (U p);")
        lines.append("    }")
        lines.append("")

        # Vorticity calculation for plot
        if PlotRequest.VORTICITY in spec.plot_requests:
            lines.append("    vorticity1")
            lines.append("    {")
            lines.append("        type            vorticity;")
            lines.append("        libs            (\"libfieldFunctionObjects.so\");")
            lines.append("        field           U;")
            lines.append("        writeControl    writeTime;")
            lines.append("    }")
            lines.append("")

        return lines

    def _available_wall_patches(
        self, spec: ObstacleFlowExperimentSpecV1
    ) -> list[str]:
        """Return the list of wall patch names present in the case."""
        patches = ["bottom", "top"]
        if spec.has_cylinder:
            patches.append("cylinder")
        if spec.has_rectangle:
            patches.append("rectangle")
        if spec.has_triangle:
            patches.append("triangle")
        if spec.has_trapezoid:
            patches.append("trapezoid")
        if spec.has_polygon:
            patches.append("polygon")
        return patches

    def _compile_fv_schemes(
        self, spec: ObstacleFlowExperimentSpecV1, is_turbulent: bool
    ) -> str:
        """Generate system/fvSchemes."""
        is_transient = spec.is_transient

        if is_transient:
            ddt = "backward"
        else:
            ddt = "steadyState"

        lines = [
            _header("dictionary", "fvSchemes"),
            "ddtSchemes",
            "{",
            f"    default         {ddt};",
            "}",
            "",
            "gradSchemes",
            "{",
            "    default         Gauss linear;",
            "    grad(U)         Gauss linear;",
            "    grad(p)         Gauss linear;",
            "}",
            "",
            "divSchemes",
            "{",
            "    default         none;",
            "    div(phi,U)      Gauss linearUpwind grad(U);",
        ]

        if is_turbulent:
            lines.extend([
                "    div(phi,k)      Gauss upwind;",
                "    div(phi,omega)  Gauss upwind;",
                "    div((nuEff*dev2(T(grad(U))))) Gauss linear;",
            ])

        lines.extend([
            "}",
            "",
            "laplacianSchemes",
            "{",
            "    default         Gauss linear limited 0.5;",
            "}",
            "",
            "interpolationSchemes",
            "{",
            "    default         linear;",
            "}",
            "",
        ])

        # wallDist required by kOmegaSST in Foundation 13
        if is_turbulent:
            lines.extend([
                "wallDist",
                "{",
                "    method          meshWave;",
                "    nRequired       false;",
                "}",
                "",
            ])

        lines.append("// ************************************************************************* //")

        return "\n".join(lines)

    def _compile_fv_solution(
        self, spec: ObstacleFlowExperimentSpecV1, is_turbulent: bool
    ) -> str:
        """Generate system/fvSolution."""
        is_transient = spec.is_transient

        if is_transient:
            lines = [
                _header("dictionary", "fvSolution"),
                "solvers",
                "{",
                "    p",
                "    {",
                "        solver          GAMG;",
                "        tolerance       1e-07;",
                "        relTol          0.01;",
                "        smoother        GaussSeidel;",
                "    }",
                "",
                "    pFinal",
                "    {",
                "        solver          GAMG;",
                "        tolerance       1e-07;",
                "        relTol          0;",
                "        smoother        GaussSeidel;",
                "    }",
                "",
                "    U",
                "    {",
                "        solver          smoothSolver;",
                "        smoother        symGaussSeidel;",
                "        tolerance       1e-08;",
                "        relTol          0.1;",
                "    }",
                "",
                "    UFinal",
                "    {",
                "        solver          smoothSolver;",
                "        smoother        symGaussSeidel;",
                "        tolerance       1e-08;",
                "        relTol          0;",
                "    }",
                "",
            ]
            if is_turbulent:
                lines.extend([
                    "    k",
                    "    {",
                    "        solver          smoothSolver;",
                    "        smoother        symGaussSeidel;",
                    "        tolerance       1e-08;",
                    "        relTol          0.1;",
                    "    }",
                    "",
                    "    kFinal",
                    "    {",
                    "        solver          smoothSolver;",
                    "        smoother        symGaussSeidel;",
                    "        tolerance       1e-08;",
                    "        relTol          0;",
                    "    }",
                    "",
                    "    omega",
                    "    {",
                    "        solver          smoothSolver;",
                    "        smoother        symGaussSeidel;",
                    "        tolerance       1e-08;",
                    "        relTol          0.1;",
                    "    }",
                    "",
                    "    omegaFinal",
                    "    {",
                    "        solver          smoothSolver;",
                    "        smoother        symGaussSeidel;",
                    "        tolerance       1e-08;",
                    "        relTol          0;",
                    "    }",
                    "",
                ])

            lines.extend([
                "}",
                "",
                "PIMPLE",
                "{",
                "    momentumPredictor yes;",
                "    nOuterCorrectors 1;",
                "    nCorrectors     2;",
                "    nNonOrthogonalCorrectors 2;",
                f"    maxCo           {_fmt(spec.simulation.max_courant_number)};",
                "}",
                "",
            ])
        else:
            lines = [
                _header("dictionary", "fvSolution"),
                "solvers",
                "{",
                "    p",
                "    {",
                "        solver          GAMG;",
                "        tolerance       1e-06;",
                "        relTol          0.01;",
                "        smoother        GaussSeidel;",
                "    }",
                "",
                "    U",
                "    {",
                "        solver          smoothSolver;",
                "        smoother        symGaussSeidel;",
                "        tolerance       1e-08;",
                "        relTol          0.1;",
                "    }",
                "",
            ]
            if is_turbulent:
                lines.extend([
                    "    k",
                    "    {",
                    "        solver          smoothSolver;",
                    "        smoother        symGaussSeidel;",
                    "        tolerance       1e-08;",
                    "        relTol          0.1;",
                    "    }",
                    "",
                    "    omega",
                    "    {",
                    "        solver          smoothSolver;",
                    "        smoother        symGaussSeidel;",
                    "        tolerance       1e-08;",
                    "        relTol          0.1;",
                    "    }",
                    "",
                ])

            lines.extend([
                "}",
                "",
                "SIMPLE",
                "{",
                "    nNonOrthogonalCorrectors 1;",
                "    consistent      yes;",
                "}",
                "",
                "relaxationFactors",
                "{",
                "    equations",
                "    {",
                "        U               0.9;",
                "        p               0.7;",
                "    }",
                "}",
                "",
            ])

        lines.append("// ************************************************************************* //")
        return "\n".join(lines)

    def _compile_decompose_par_dict(
        self, spec: ObstacleFlowExperimentSpecV1
    ) -> str:
        """Generate system/decomposeParDict for parallel decomposition."""
        lines = [
            _header("dictionary", "decomposeParDict"),
            "numberOfSubdomains  2;",
            "method          simple;",
            "",
            "simpleCoeffs",
            "{",
            "    n               (2 1 1);",
            "    delta           0.001;",
            "}",
            "",
            "// ************************************************************************* //",
        ]
        return "\n".join(lines)

    # --- Utility ---

    def _determine_preprocessing(
        self, spec: ObstacleFlowExperimentSpecV1
    ) -> tuple[str, ...]:
        """Determine preprocessing steps needed."""
        steps = ["blockMesh"]
        if spec.has_cylinder or spec.has_rectangle or spec.has_triangle or spec.has_trapezoid or spec.has_polygon:
            steps.append("snappyHexMesh")
        steps.append("checkMesh")
        return tuple(steps)

    def _compile_polygon(self, polygon: PolygonSpec) -> str:
        """Generate a polygonal prism STL surface for snappyHexMesh.

        Creates a closed prism from the polygon's 2D vertex list by
        extruding in z by *thickness*.  The polygon must have >= 3
        vertices.

        Triangulation uses a fan from vertex 0 for bottom and top faces.
        Side faces are quads split into 2 triangles each, with outward-
        facing normals computed from the edge direction.
        """
        verts_2d = polygon.vertices
        n = len(verts_2d)
        if n < 3:
            raise CompilationError(
                f"Polygon requires at least 3 vertices, got {n}"
            )

        z0 = 0.0
        z1 = polygon.thickness

        # Build 3D vertices: n bottom + n top
        verts: list[tuple[float, float, float]] = []
        for v in verts_2d:
            verts.append((float(v[0]), float(v[1]), z0))
        for v in verts_2d:
            verts.append((float(v[0]), float(v[1]), z1))

        triangles: list[tuple[tuple[float, float, float], int, int, int]] = []

        # Bottom face (normal -z): fan from vertex 0
        for i in range(1, n - 1):
            triangles.append(((0.0, 0.0, -1.0), 0, i + 1, i))

        # Top face (normal +z): fan from vertex n
        for i in range(1, n - 1):
            triangles.append(((0.0, 0.0, 1.0), n, n + i, n + i + 1))

        # Side faces: quad (i, i+1, i+1+n, i+n) split into 2 triangles
        for i in range(n):
            j = (i + 1) % n
            dx = verts_2d[j][0] - verts_2d[i][0]
            dy = verts_2d[j][1] - verts_2d[i][1]
            length = math.sqrt(dx * dx + dy * dy)
            if length > 0:
                nx = dy / length
                ny = -dx / length
            else:
                nx, ny = 0.0, 0.0
            triangles.append(((nx, ny, 0.0), i, j, n + j))
            triangles.append(((nx, ny, 0.0), i, n + j, n + i))

        lines: list[str] = []
        lines.append("solid polygon")
        for normal, i0, i1, i2 in triangles:
            nx, ny, nz = normal
            v0 = verts[i0]
            v1 = verts[i1]
            v2 = verts[i2]
            lines.append(f"  facet normal {nx:.6e} {ny:.6e} {nz:.6e}")
            lines.append("    outer loop")
            lines.append(f"      vertex {v0[0]:.6e} {v0[1]:.6e} {v0[2]:.6e}")
            lines.append(f"      vertex {v1[0]:.6e} {v1[1]:.6e} {v1[2]:.6e}")
            lines.append(f"      vertex {v2[0]:.6e} {v2[1]:.6e} {v2[2]:.6e}")
            lines.append("    endloop")
            lines.append("  endfacet")
        lines.append("endsolid polygon")
        return "\n".join(lines)

    def _determine_required_outputs(
        self, spec: ObstacleFlowExperimentSpecV1
    ) -> tuple[str, ...]:
        """Determine required output fields based on observables."""
        outputs = ["velocity", "pressure", "residuals"]
        if PlotRequest.VORTICITY in spec.plot_requests:
            outputs.append("vorticity")
        if any(o.type in (ObservableType.CYLINDER_DRAG, ObservableType.CYLINDER_LIFT) for o in spec.observables):
            outputs.append("forceCoefficients")
        if any(o.type == ObservableType.WALL_SHEAR_STRESS for o in spec.observables):
            outputs.append("wallShearStress")
        if any(
            o.type in (ObservableType.POINT_VELOCITY, ObservableType.SECTION_MEAN_VELOCITY)
            for o in spec.observables
        ):
            outputs.append("probes")
        return tuple(outputs)


def _int_str(v: float) -> str:
    """Convert a float to an integer string for naming."""
    return str(int(v))


# ---------------------------------------------------------------------------
# Compiler Registry
# ---------------------------------------------------------------------------


class ObstacleFlowCompilerRegistry:
    """Registry for obstacle flow compilers.

    Currently only has one compiler (V1), but structured for future
    extensibility.
    """

    def __init__(self) -> None:
        self._compilers: list[ObstacleFlowCompiler] = []
        self._register_defaults()

    def _register_defaults(self) -> None:
        self.register(ObstacleFlowCompiler())

    def register(self, compiler: ObstacleFlowCompiler) -> None:
        self._compilers.append(compiler)

    def resolve(self, spec: ObstacleFlowExperimentSpecV1) -> ObstacleFlowCompiler | None:
        """Find a compiler that can handle the given spec."""
        for compiler in self._compilers:
            return compiler  # V1 has single compiler
        return None

    def compile(
        self, spec: ObstacleFlowExperimentSpecV1
    ) -> tuple[ObstacleFlowCompiledCase, CompilationManifest]:
        """Compile the spec using the registered compiler."""
        compiler = self.resolve(spec)
        if compiler is None:
            raise CompilationError("No compiler available for the given spec")
        return compiler.compile(spec)


__all__ = [
    "CompilationError",
    "CompilationManifest",
    "ObstacleFlowCompiledCase",
    "ObstacleFlowCompiler",
    "ObstacleFlowCompilerRegistry",
]
