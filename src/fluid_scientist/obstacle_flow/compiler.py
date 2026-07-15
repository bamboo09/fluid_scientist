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
    PressureGradientUnit,
    SimulationSpec,
    TemporalType,
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
    """Create a deterministic tar.gz archive from file contents."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.GNU_FORMAT) as tar:
        for name in sorted(files.keys()):
            content = files[name].encode("utf-8")
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            info.mtime = 0
            info.mode = 0o644
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            tar.addfile(info, io.BytesIO(content))
    return buf.getvalue()


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

    def _compile_control_dict(self, spec: ObstacleFlowExperimentSpecV1) -> str:
        """Generate system/controlDict."""
        sim = spec.simulation
        is_transient = spec.is_transient

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
            end_time = 1000.0
            delta_t = 1.0
            write_interval = 100

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

        # Probes for point velocity
        point_obs = [o for o in spec.observables if o.type == ObservableType.POINT_VELOCITY]
        if point_obs:
            lines.append("    probes1")
            lines.append("    {")
            lines.append("        type            probes;")
            lines.append("        libs            (\"libsampling.so\");")
            lines.append("        writeControl    timeStep;")
            lines.append("        writeInterval   1;")
            lines.append("        fields          (U);")
            lines.append("        probeLocations")
            lines.append("        (")
            for obs in point_obs:
                if obs.point is not None:
                    p = obs.point
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
        if spec.has_cylinder or spec.has_rectangle or spec.has_triangle:
            steps.append("snappyHexMesh")
        steps.append("checkMesh")
        return tuple(steps)

    def _determine_required_outputs(
        self, spec: ObstacleFlowExperimentSpecV1
    ) -> tuple[str, ...]:
        """Determine required output fields based on observables."""
        outputs = ["velocity", "pressure", "residuals"]
        if PlotRequest.VORTICITY in spec.plot_requests:
            outputs.append("vorticity")
        if any(o.type in (ObservableType.CYLINDER_DRAG, ObservableType.CYLINDER_LIFT) for o in spec.observables):
            outputs.append("forceCoefficients")
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
