"""Deterministic OpenFOAM Foundation 13 case compilers."""

import gzip
import hashlib
import io
import math
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path

from fluid_scientist.adapters.custom_openfoam import (
    CustomCaseManifest,
    validate_custom_case_archive,
)
from fluid_scientist.adapters.openfoam import (
    LaminarPipeCase as RendererPipeCase,
)
from fluid_scientist.adapters.openfoam import OpenFOAM13CaseRenderer
from fluid_scientist.experiment_planning.models import (
    CavityExperimentPlan,
    CustomExperimentPlan,
    CylinderExperimentPlan,
    ExperimentPlan,
    PipeExperimentPlan,
)
from fluid_scientist.experiment_planning.registry import (
    CUSTOM_UPLOAD,
    CustomUploadMarker,
    get_experiment_capability,
)


class UnsupportedCompilation(TypeError):
    """Raised when a plan must follow a route other than built-in compilation."""


@dataclass(frozen=True)
class CompiledCase:
    """Validated, content-addressed OpenFOAM case archive."""

    archive: bytes
    archive_sha256: str
    manifest: CustomCaseManifest
    experiment_type: str
    preprocessing: tuple[str, ...]
    required_outputs: tuple[str, ...]

    @property
    def digest(self) -> str:
        """Compatibility alias for the content-addressed archive digest."""

        return self.archive_sha256


def compile_plan(
    plan: ExperimentPlan
    | PipeExperimentPlan
    | CylinderExperimentPlan
    | CavityExperimentPlan
    | CustomExperimentPlan,
) -> CompiledCase:
    """Compile a strict plan variant through its registered route."""

    variant = plan.root if isinstance(plan, ExperimentPlan) else plan
    capability = get_experiment_capability(variant.experiment_type)
    if capability.compiler is CUSTOM_UPLOAD or isinstance(
        capability.compiler, CustomUploadMarker
    ):
        raise UnsupportedCompilation(
            "custom_openfoam requires a separately reviewed archive upload"
        )
    compiled = capability.compiler(variant)
    if not isinstance(compiled, CompiledCase):
        raise TypeError("registered compiler returned an invalid result")
    return compiled


def compile_pipe_plan(plan: object) -> CompiledCase:
    if not isinstance(plan, PipeExperimentPlan):
        raise TypeError("pipe compiler requires PipeExperimentPlan")
    spec = RendererPipeCase(**plan.case.model_dump())
    with tempfile.TemporaryDirectory(prefix="fluid-case-") as temporary:
        case_root = Path(temporary) / "rendered"
        OpenFOAM13CaseRenderer(Path(temporary)).render("rendered", spec)
        files = {
            path.relative_to(case_root).as_posix(): path.read_text(encoding="utf-8")
            for path in case_root.rglob("*")
            if path.is_file()
        }
    files["0/U"] = _pipe_velocity_field(
        velocity=plan.case.mean_velocity_m_s,
        diameter=plan.case.diameter_m,
    )
    return _compiled(plan.experiment_type, files)


def compile_cylinder_plan(plan: object) -> CompiledCase:
    if not isinstance(plan, CylinderExperimentPlan):
        raise TypeError("cylinder compiler requires CylinderExperimentPlan")
    case = plan.case
    diameter = case.diameter_m
    radius = diameter / 2.0
    upstream = case.domain_upstream_diameters * diameter
    downstream = case.domain_downstream_diameters * diameter
    transverse = case.domain_transverse_diameters * diameter
    thickness = diameter * 0.1
    delta_t = case.time_step_s
    if delta_t is None:
        delta_t = 0.25 * diameter / (case.mean_velocity_m_s * case.cells_radial)
    files = {
        "0/U": _cylinder_velocity_field(case.mean_velocity_m_s),
        "0/p": _cylinder_pressure_field(),
        "constant/momentumTransport": _momentum_transport(),
        "constant/physicalProperties": _physical_properties(
            case.kinematic_viscosity_m2_s
        ),
        "system/blockMeshDict": _cylinder_block_mesh(
            radius=radius,
            upstream=upstream,
            downstream=downstream,
            transverse=transverse,
            thickness=thickness,
            circumferential_cells=case.cells_radial,
            wake_cells=case.cells_wake,
        ),
        "system/mirrorMeshDict": _mirror_mesh_dict(),
        "system/controlDict": _cylinder_control_dict(
            end_time=case.end_time_s,
            delta_t=delta_t,
            adjust_time_step=case.max_courant is not None,
            max_courant=case.max_courant or 1.0,
            density=case.density_kg_m3,
            velocity=case.mean_velocity_m_s,
            diameter=diameter,
        ),
        "system/fvSchemes": _transient_fv_schemes(),
        "system/fvSolution": _transient_fv_solution(
            plan.convergence_targets.residual_tolerance
        ),
    }
    return _compiled(plan.experiment_type, files)


def compile_cavity_plan(plan: object) -> CompiledCase:
    if not isinstance(plan, CavityExperimentPlan):
        raise TypeError("cavity compiler requires CavityExperimentPlan")
    case = plan.case
    files = {
        "0/U": _cavity_velocity_field(case.lid_velocity_m_s),
        "0/p": _cavity_pressure_field(),
        "constant/momentumTransport": _momentum_transport(),
        "constant/physicalProperties": _physical_properties(
            case.kinematic_viscosity_m2_s
        ),
        "system/blockMeshDict": _cavity_block_mesh(
            side=case.side_length_m,
            cells=case.cells_per_side,
        ),
        "system/controlDict": _cavity_control_dict(
            end_time=case.end_time_s,
            side=case.side_length_m,
        ),
        "system/fvSchemes": _transient_fv_schemes(),
        "system/fvSolution": _transient_fv_solution(
            plan.convergence_targets.residual_tolerance
        ),
    }
    return _compiled(plan.experiment_type, files)


def _compiled(experiment_type: str, files: dict[str, str]) -> CompiledCase:
    normalized = {name: _normalize(text) for name, text in files.items()}
    archive = _deterministic_tar_gz(normalized)
    manifest = validate_custom_case_archive(archive)
    capability = get_experiment_capability(experiment_type)
    return CompiledCase(
        archive=archive,
        archive_sha256="sha256:" + hashlib.sha256(archive).hexdigest(),
        manifest=manifest,
        experiment_type=experiment_type,
        preprocessing=capability.preprocessing,
        required_outputs=capability.required_outputs,
    )


def _deterministic_tar_gz(files: dict[str, str]) -> bytes:
    tar_output = io.BytesIO()
    with tarfile.open(fileobj=tar_output, mode="w", format=tarfile.USTAR_FORMAT) as bundle:
        for name in sorted(files):
            payload = files[name].encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            info.mode = 0o644
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mtime = 0
            bundle.addfile(info, io.BytesIO(payload))
    compressed = io.BytesIO()
    with gzip.GzipFile(fileobj=compressed, mode="wb", filename="", mtime=0) as stream:
        stream.write(tar_output.getvalue())
    return compressed.getvalue()


def _normalize(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").rstrip() + "\n"


def _number(value: float) -> str:
    return f"{value:.12g}"


def _header(object_name: str, *, field_class: str = "dictionary", location: str = "") -> str:
    location_line = f'    location    "{location}";\n' if location else ""
    return (
        "FoamFile\n{\n"
        "    version     2.0;\n"
        "    format      ascii;\n"
        f"    class       {field_class};\n"
        f"{location_line}"
        f"    object      {object_name};\n"
        "}\n"
    )


def _pipe_velocity_field(*, velocity: float, diameter: float) -> str:
    flow_rate = velocity * math.pi * diameter**2 / 4.0
    return _header("U", field_class="volVectorField", location="0") + f"""
dimensions      [0 1 -1 0 0 0 0];
internalField   uniform ({_number(velocity)} 0 0);
boundaryField
{{
    #includeEtc "caseDicts/setConstraintTypes"
    inlet
    {{
        type                flowRateInletVelocity;
        volumetricFlowRate  constant {_number(flow_rate)};
        value               uniform ({_number(velocity)} 0 0);
    }}
    outlet
    {{
        type                pressureInletOutletVelocity;
        value               $internalField;
    }}
    walls
    {{
        type                noSlip;
    }}
}}
"""


def _momentum_transport() -> str:
    return _header("momentumTransport", location="constant") + "simulationType laminar;\n"


def _physical_properties(nu: float) -> str:
    return _header("physicalProperties", location="constant") + (
        "viscosityModel  constant;\n"
        f"nu              [0 2 -1 0 0 0 0] {_number(nu)};\n"
    )


def _cylinder_velocity_field(velocity: float) -> str:
    value = _number(velocity)
    return _header("U", field_class="volVectorField", location="0") + f"""
dimensions      [0 1 -1 0 0 0 0];
internalField   uniform ({value} 0 0);
boundaryField
{{
    inlet {{ type fixedValue; value uniform ({value} 0 0); }}
    outlet {{ type pressureInletOutletVelocity; value $internalField; }}
    farfield {{ type freestream; freestreamValue uniform ({value} 0 0); }}
    cylinder {{ type noSlip; }}
    symmetryPlane {{ type symmetryPlane; }}
    frontAndBack {{ type empty; }}
}}
"""


def _cylinder_pressure_field() -> str:
    return _header("p", field_class="volScalarField", location="0") + """
dimensions      [0 2 -2 0 0 0 0];
internalField   uniform 0;
boundaryField
{
    inlet { type zeroGradient; }
    outlet { type fixedValue; value uniform 0; }
    farfield { type freestreamPressure; }
    cylinder { type zeroGradient; }
    symmetryPlane { type symmetryPlane; }
    frontAndBack { type empty; }
}
"""


def _cylinder_block_mesh(
    *,
    radius: float,
    upstream: float,
    downstream: float,
    transverse: float,
    thickness: float,
    circumferential_cells: int,
    wake_cells: int,
) -> str:
    diagonal = radius / math.sqrt(2.0)
    arc_x = radius * math.cos(math.pi / 8)
    arc_y = radius * math.sin(math.pi / 8)
    points = (
        (-radius, 0.0),
        (-diagonal, diagonal),
        (0.0, radius),
        (diagonal, diagonal),
        (radius, 0.0),
        (-upstream, 0.0),
        (-upstream, transverse),
        (0.0, transverse),
        (downstream, transverse),
        (downstream, 0.0),
    )
    vertices = []
    for z in (0.0, thickness):
        vertices.extend(
            f"    ({_number(x)} {_number(y)} {_number(z)})" for x, y in points
        )
    circum = max(4, circumferential_cells // 4)
    outward = max(8, wake_cells // 4)
    blocks = []
    for index in range(4):
        blocks.append(
            "    hex "
            f"({index} {index + 1} {index + 6} {index + 5} "
            f"{index + 10} {index + 11} {index + 16} {index + 15}) "
            f"({circum} {outward} 1) simpleGrading (1 4 1)"
        )
    return _header("blockMeshDict") + f"""
convertToMeters 1;
vertices
(
{chr(10).join(vertices)}
);
blocks
(
{chr(10).join(blocks)}
);
edges
(
    arc 0 1 ({_number(-arc_x)} {_number(arc_y)} 0)
    arc 1 2 ({_number(-arc_y)} {_number(arc_x)} 0)
    arc 2 3 ({_number(arc_y)} {_number(arc_x)} 0)
    arc 3 4 ({_number(arc_x)} {_number(arc_y)} 0)
    arc 10 11 ({_number(-arc_x)} {_number(arc_y)} {_number(thickness)})
    arc 11 12 ({_number(-arc_y)} {_number(arc_x)} {_number(thickness)})
    arc 12 13 ({_number(arc_y)} {_number(arc_x)} {_number(thickness)})
    arc 13 14 ({_number(arc_x)} {_number(arc_y)} {_number(thickness)})
);
boundary
(
    inlet {{ type patch; faces ((5 6 16 15)); }}
    outlet {{ type patch; faces ((8 9 19 18)); }}
    farfield {{ type patch; faces ((6 7 17 16) (7 8 18 17)); }}
    cylinder {{ type wall; faces ((0 10 11 1) (1 11 12 2) (2 12 13 3) (3 13 14 4)); }}
    symmetryPlane {{ type symmetryPlane; faces ((0 5 15 10) (4 14 19 9)); }}
    frontAndBack
    {{
        type empty;
        faces
        (
            (0 1 6 5) (1 2 7 6) (2 3 8 7) (3 4 9 8)
            (10 15 16 11) (11 16 17 12) (12 17 18 13) (13 18 19 14)
        );
    }}
);
"""


def _mirror_mesh_dict() -> str:
    return _header("mirrorMeshDict") + """
mirrorPlane
{
    planeType pointAndNormal;
    pointAndNormalDict
    {
        point  (0 0 0);
        normal (0 1 0);
    }
}
"""


def _cylinder_control_dict(
    *,
    end_time: float,
    delta_t: float,
    adjust_time_step: bool,
    max_courant: float,
    density: float,
    velocity: float,
    diameter: float,
) -> str:
    write_interval = max(delta_t, end_time / 100.0)
    adjust = "yes" if adjust_time_step else "no"
    return _header("controlDict", location="system") + f"""
solver          incompressibleFluid;
startFrom       startTime;
startTime       0;
stopAt          endTime;
endTime         {_number(end_time)};
deltaT          {_number(delta_t)};
adjustTimeStep  {adjust};
maxCo           {_number(max_courant)};
writeControl    adjustableRunTime;
writeInterval   {_number(write_interval)};
purgeWrite      0;
writeFormat     ascii;
writePrecision  10;
runTimeModifiable true;
functions
{{
    forces
    {{
        type forces;
        libs ("libforces.so");
        patches (cylinder);
        rho rhoInf;
        rhoInf {_number(density)};
        CofR (0 0 0);
        writeControl timeStep;
        writeInterval 1;
    }}
    forceCoeffs
    {{
        type forceCoeffs;
        libs ("libforces.so");
        patches (cylinder);
        rho rhoInf;
        rhoInf {_number(density)};
        magUInf {_number(velocity)};
        lRef {_number(diameter)};
        Aref {_number(diameter)};
        dragDir (1 0 0);
        liftDir (0 1 0);
        pitchAxis (0 0 1);
        CofR (0 0 0);
        writeControl timeStep;
        writeInterval 1;
    }}
    residuals
    {{
        type residuals;
        libs ("libutilityFunctionObjects.so");
        fields (U p);
        writeControl timeStep;
        writeInterval 1;
    }}
}}
"""


def _transient_fv_schemes() -> str:
    return _header("fvSchemes", location="system") + """
ddtSchemes { default backward; }
gradSchemes { default Gauss linear; }
divSchemes
{
    default none;
    div(phi,U) bounded Gauss linearUpwind grad(U);
    div((nuEff*dev2(T(grad(U))))) Gauss linear;
}
laplacianSchemes { default Gauss linear corrected; }
interpolationSchemes { default linear; }
snGradSchemes { default corrected; }
"""


def _transient_fv_solution(tolerance: float) -> str:
    residual = _number(tolerance)
    return _header("fvSolution", location="system") + f"""
solvers
{{
    p
    {{
        solver GAMG;
        smoother GaussSeidel;
        tolerance {residual};
        relTol 0.01;
    }}
    U
    {{
        solver smoothSolver;
        smoother symGaussSeidel;
        tolerance {residual};
        relTol 0.01;
    }}
}}
PIMPLE
{{
    momentumPredictor yes;
    nOuterCorrectors 1;
    nCorrectors 2;
    nNonOrthogonalCorrectors 0;
}}
"""


def _cavity_velocity_field(velocity: float) -> str:
    value = _number(velocity)
    return _header("U", field_class="volVectorField", location="0") + f"""
dimensions      [0 1 -1 0 0 0 0];
internalField   uniform (0 0 0);
boundaryField
{{
    movingLid {{ type fixedValue; value uniform ({value} 0 0); }}
    fixedWalls {{ type noSlip; }}
    frontAndBack {{ type empty; }}
}}
"""


def _cavity_pressure_field() -> str:
    return _header("p", field_class="volScalarField", location="0") + """
dimensions      [0 2 -2 0 0 0 0];
internalField   uniform 0;
boundaryField
{
    movingLid { type zeroGradient; }
    fixedWalls { type zeroGradient; }
    frontAndBack { type empty; }
}
"""


def _cavity_block_mesh(*, side: float, cells: int) -> str:
    thickness = side / cells
    side_value = _number(side)
    thick_value = _number(thickness)
    return _header("blockMeshDict") + f"""
convertToMeters 1;
vertices
(
    (0 0 0) ({side_value} 0 0) ({side_value} {side_value} 0) (0 {side_value} 0)
    (0 0 {thick_value}) ({side_value} 0 {thick_value})
    ({side_value} {side_value} {thick_value}) (0 {side_value} {thick_value})
);
blocks (hex (0 1 2 3 4 5 6 7) ({cells} {cells} 1) simpleGrading (1 1 1));
edges ();
boundary
(
    movingLid {{ type wall; faces ((3 7 6 2)); }}
    fixedWalls {{ type wall; faces ((0 1 5 4) (0 4 7 3) (1 2 6 5)); }}
    frontAndBack {{ type empty; faces ((0 3 2 1) (4 5 6 7)); }}
);
"""


def _cavity_control_dict(*, end_time: float, side: float) -> str:
    write_interval = end_time / 50.0
    quarter = side / 4.0
    half = side / 2.0
    three_quarters = side * 3.0 / 4.0
    return _header("controlDict", location="system") + f"""
solver          incompressibleFluid;
startFrom       startTime;
startTime       0;
stopAt          endTime;
endTime         {_number(end_time)};
deltaT          {_number(end_time / 1000.0)};
adjustTimeStep  yes;
maxCo           0.5;
writeControl    adjustableRunTime;
writeInterval   {_number(write_interval)};
purgeWrite      0;
writeFormat     ascii;
writePrecision  10;
runTimeModifiable true;
functions
{{
    velocityProbes
    {{
        type probes;
        libs ("libsampling.so");
        fields (U p);
        probeLocations
        (
            ({_number(quarter)} {_number(half)} 0)
            ({_number(half)} {_number(half)} 0)
            ({_number(three_quarters)} {_number(half)} 0)
        );
        writeControl timeStep;
        writeInterval 1;
    }}
    residuals
    {{
        type residuals;
        libs ("libutilityFunctionObjects.so");
        fields (U p);
        writeControl timeStep;
        writeInterval 1;
    }}
}}
"""
