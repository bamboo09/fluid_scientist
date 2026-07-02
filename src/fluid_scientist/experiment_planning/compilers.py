"""Deterministic OpenFOAM Foundation 13 case compilers."""

import gzip
import hashlib
import io
import json
import math
import tarfile
from dataclasses import dataclass

from fluid_scientist.adapters.custom_openfoam import (
    CustomCaseManifest,
    validate_custom_case_archive,
)
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


class CompilationError(ValueError):
    """Raised when a valid plan cannot produce a safe runnable case."""


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
    case = plan.case
    targets = plan.convergence_targets
    files = {
        "0/U": _pipe_velocity_profile_field(
            velocity=case.mean_velocity_m_s,
            radial_cells=case.radial_cells,
        ),
        "0/p": _pipe_pressure_field(),
        "constant/momentumTransport": _momentum_transport(),
        "constant/physicalProperties": _physical_properties(
            case.kinematic_viscosity_m2_s
        ),
        "system/blockMeshDict": _pipe_block_mesh(
            diameter=case.diameter_m,
            length=case.length_m,
            axial_cells=case.axial_cells,
            radial_cells=case.radial_cells,
        ),
        "system/controlDict": _pipe_control_dict(),
        "system/fvSchemes": _steady_fv_schemes(),
        "system/fvSolution": _pipe_fv_solution(targets.residual_tolerance),
    }
    return _compiled(plan, files)


def compile_cylinder_plan(plan: object) -> CompiledCase:
    if not isinstance(plan, CylinderExperimentPlan):
        raise TypeError("cylinder compiler requires CylinderExperimentPlan")
    case = plan.case
    diameter = case.diameter_m
    radius = diameter / 2.0
    upstream = case.domain_upstream_diameters * diameter
    downstream = case.domain_downstream_diameters * diameter
    transverse = case.domain_transverse_diameters * diameter
    extrusion_span = diameter * 0.1
    estimated_cell_size = _cylinder_minimum_cell_size(
        radius=radius,
        upstream=upstream,
        downstream=downstream,
        transverse=transverse,
        thickness=extrusion_span,
        radial_cells=case.cells_radial,
        wake_cells=case.cells_wake,
    )
    max_courant = case.max_courant or 1.0
    stable_delta_t = max_courant * estimated_cell_size / case.mean_velocity_m_s
    if stable_delta_t < 1e-12:
        raise CompilationError("required cylinder time step is below the safe representable limit")
    delta_t = case.time_step_s
    if delta_t is not None and delta_t > stable_delta_t:
        raise CompilationError(
            "initial cylinder time step exceeds the conservative Courant limit"
        )
    if delta_t is None:
        delta_t = 0.5 * stable_delta_t
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
            thickness=extrusion_span,
            circumferential_cells=case.cells_radial,
            wake_cells=case.cells_wake,
        ),
        "system/mirrorMeshDict": _mirror_mesh_dict(diameter),
        "system/controlDict": _cylinder_control_dict(
            end_time=case.end_time_s,
            delta_t=delta_t,
            adjust_time_step=True,
            max_courant=max_courant,
            density=case.density_kg_m3,
            velocity=case.mean_velocity_m_s,
            diameter=diameter,
            extrusion_span=extrusion_span,
        ),
        "system/fvSchemes": _transient_fv_schemes(),
        "system/fvSolution": _transient_fv_solution(
            plan.convergence_targets.residual_tolerance
        ),
    }
    return _compiled(plan, files)


def compile_cavity_plan(plan: object) -> CompiledCase:
    if not isinstance(plan, CavityExperimentPlan):
        raise TypeError("cavity compiler requires CavityExperimentPlan")
    case = plan.case
    thickness = case.side_length_m / case.cells_per_side
    stable_delta_t = 0.5 * thickness / case.lid_velocity_m_s
    if stable_delta_t < 1e-12:
        raise CompilationError("required cavity time step is below the safe representable limit")
    delta_t = min(case.end_time_s / 1000.0, stable_delta_t)
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
            thickness=thickness,
            delta_t=delta_t,
        ),
        "system/fvSchemes": _transient_fv_schemes(),
        "system/fvSolution": _transient_fv_solution(
            plan.convergence_targets.residual_tolerance,
            pressure_reference=True,
        ),
    }
    return _compiled(plan, files)


def _compiled(
    plan: PipeExperimentPlan | CylinderExperimentPlan | CavityExperimentPlan,
    files: dict[str, str],
) -> CompiledCase:
    experiment_type = plan.experiment_type
    capability = get_experiment_capability(experiment_type)
    requested_outputs = tuple(
        output.value if hasattr(output, "value") else str(output)
        for output in plan.requested_outputs
    )
    unsupported = sorted(set(requested_outputs) - set(capability.required_outputs))
    if unsupported:
        raise CompilationError("unsupported requested outputs: " + ", ".join(unsupported))
    files = {**files, "fluidScientist/plan.json": _plan_metadata(plan, requested_outputs)}
    normalized = {name: _normalize(text) for name, text in files.items()}
    archive = _deterministic_tar_gz(normalized)
    manifest = validate_custom_case_archive(archive)
    return CompiledCase(
        archive=archive,
        archive_sha256="sha256:" + hashlib.sha256(archive).hexdigest(),
        manifest=manifest,
        experiment_type=experiment_type,
        preprocessing=capability.preprocessing,
        required_outputs=requested_outputs,
    )


def _plan_metadata(
    plan: PipeExperimentPlan | CylinderExperimentPlan | CavityExperimentPlan,
    requested_outputs: tuple[str, ...],
) -> str:
    payload = plan.model_dump(mode="json")
    metadata: dict[str, object] = {
        "schema_version": 1,
        "experiment_type": plan.experiment_type,
        "base_case": payload["case"],
        "parameter_sweeps": payload.get("parameter_sweeps", []),
        "convergence_targets": payload["convergence_targets"],
        "requested_outputs": list(requested_outputs),
        "compilation": {
            "mode": "approved_base_case",
            "sweep_expansion_owner": "task7",
        },
        "output_derivations": {},
    }
    if isinstance(plan, CylinderExperimentPlan) and "strouhal_number" in requested_outputs:
        metadata["output_derivations"] = {
            "strouhal_number": "derived from forceCoeffs lift history and shedding frequency"
        }
    return json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


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


def _pipe_velocity_profile_field(*, velocity: float, radial_cells: int) -> str:
    values = []
    for index in range(radial_cells):
        average = 2.0 * velocity * (
            1.0 - (index**2 + (index + 1) ** 2) / (2.0 * radial_cells**2)
        )
        values.append(f"        ({_number(average)} 0 0)")
    return _header("U", field_class="volVectorField", location="0") + f"""
dimensions      [0 1 -1 0 0 0 0];
internalField   uniform ({_number(velocity)} 0 0);
boundaryField
{{
    inlet
    {{
        type fixedValue;
        value nonuniform List<vector>
        {radial_cells}
        (
{chr(10).join(values)}
        );
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
    side1 {{ type wedge; }}
    side2 {{ type wedge; }}
}}
"""


def _pipe_pressure_field() -> str:
    return _header("p", field_class="volScalarField", location="0") + """
dimensions      [0 2 -2 0 0 0 0];
internalField   uniform 0;
boundaryField
{
    inlet { type zeroGradient; }
    outlet { type fixedValue; value uniform 0; }
    walls { type zeroGradient; }
    side1 { type wedge; }
    side2 { type wedge; }
}
"""


def _pipe_block_mesh(
    *, diameter: float, length: float, axial_cells: int, radial_cells: int
) -> str:
    radius = diameter / 2.0
    half_angle = math.radians(5.0)
    y = radius * math.sin(half_angle)
    z = radius * math.cos(half_angle)
    length_value = _number(length)
    radius_value = _number(radius)
    y_value = _number(y)
    z_value = _number(z)
    return _header("blockMeshDict") + f"""
convertToMeters 1;
radius {radius_value};
length {length_value};
vertices
(
    (0 0 0)
    ({length_value} 0 0)
    ({length_value} 0 0)
    (0 0 0)
    (0 -{y_value} {z_value})
    ({length_value} -{y_value} {z_value})
    ({length_value} {y_value} {z_value})
    (0 {y_value} {z_value})
);
blocks
(
    hex (0 1 2 3 4 5 6 7) ({axial_cells} 1 {radial_cells})
        simpleGrading (1 1 1)
);
edges
(
    arc 4 7 (0 0 {radius_value})
    arc 5 6 ({length_value} 0 {radius_value})
);
boundary
(
    inlet {{ type patch; faces ((0 4 7 3)); }}
    outlet {{ type patch; faces ((1 2 6 5)); }}
    side1 {{ type wedge; faces ((0 1 5 4)); }}
    side2 {{ type wedge; faces ((7 6 2 3)); }}
    walls {{ type wall; faces ((4 5 6 7) (3 2 1 0)); }}
);
"""


def _pipe_control_dict() -> str:
    return _header("controlDict", location="system") + """
solver          incompressibleFluid;
startFrom       startTime;
startTime       0;
stopAt          endTime;
endTime         2000;
deltaT          1;
writeControl    timeStep;
writeInterval   2000;
purgeWrite      1;
writeFormat     ascii;
writePrecision  10;
writeCompression off;
timeFormat      general;
timePrecision   6;
runTimeModifiable true;
functions
{
    pressureDrop
    {
        type fieldValueDelta;
        libs ("libfieldFunctionObjects.so");
        writeControl timeStep;
        writeInterval 1;
        operation subtract;
        region1
        {
            type surfaceFieldValue;
            libs ("libfieldFunctionObjects.so");
            writeControl timeStep;
            writeInterval 1;
            writeFields false;
            operation areaAverage;
            fields (p);
            patch inlet;
        }
        region2
        {
            type surfaceFieldValue;
            libs ("libfieldFunctionObjects.so");
            writeControl timeStep;
            writeInterval 1;
            writeFields false;
            operation areaAverage;
            fields (p);
            patch outlet;
        }
    }
    inletFlow
    {
        type surfaceFieldValue;
        libs ("libfieldFunctionObjects.so");
        writeControl timeStep;
        writeInterval 1;
        writeFields false;
        operation sum;
        fields (phi);
        patch inlet;
    }
    outletFlow
    {
        type surfaceFieldValue;
        libs ("libfieldFunctionObjects.so");
        writeControl timeStep;
        writeInterval 1;
        writeFields false;
        operation sum;
        fields (phi);
        patch outlet;
    }
    residuals
    {
        type residuals;
        libs ("libutilityFunctionObjects.so");
        fields (U p);
        writeControl timeStep;
        writeInterval 1;
    }
}
"""


def _steady_fv_schemes() -> str:
    return _header("fvSchemes", location="system") + """
ddtSchemes { default steadyState; }
gradSchemes { default Gauss linear; }
divSchemes
{
    default none;
    div(phi,U) bounded Gauss limitedLinearV 1;
    div((nuEff*dev2(T(grad(U))))) Gauss linear;
}
laplacianSchemes { default Gauss linear corrected; }
interpolationSchemes { default linear; }
snGradSchemes { default corrected; }
"""


def _pipe_fv_solution(tolerance: float) -> str:
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
        relTol 0;
    }}
}}
SIMPLE
{{
    nNonOrthogonalCorrectors 0;
    residualControl
    {{
        p               {residual};
        U               {residual};
    }}
}}
relaxationFactors
{{
    fields {{ p 0.3; }}
    equations {{ U 0.7; }}
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
    cylinder {{ type noSlip; }}
    mirrorPlane {{ type symmetryPlane; }}
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
    cylinder { type zeroGradient; }
    mirrorPlane { type symmetryPlane; }
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
    diameter = 2.0 * radius
    layer = 2.0 * diameter
    x_join = min(0.4 * downstream, downstream - diameter)
    y_join = (layer + transverse) / 2.0
    z_min = -thickness / 2.0
    z_max = thickness / 2.0
    points = (
        (-upstream, 0.0),
        (-layer, 0.0),
        (-radius, 0.0),
        (radius, 0.0),
        (layer, 0.0),
        (x_join, 0.0),
        (downstream, 0.0),
        (0.0, radius),
        (0.0, layer),
        (x_join, y_join),
        (downstream, y_join),
        (0.0, transverse),
        (x_join, transverse),
        (downstream, transverse),
    )
    vertices = []
    for z in (z_min, z_max):
        vertices.extend(
            f"    ({_number(x)} {_number(y)} {_number(z)})" for x, y in points
        )
    circum = max(4, circumferential_cells // 2)
    join_cells = max(1, wake_cells // 3)
    outlet_cells = wake_cells - join_cells
    diagonal = radius / math.sqrt(2.0)
    layer_diagonal = layer / math.sqrt(2.0)
    return _header("blockMeshDict") + f"""
convertToMeters 1;
extrusionSpan {_number(thickness)};
radialCells {circumferential_cells};
wakeCells {wake_cells};
vertices
(
{chr(10).join(vertices)}
);
blocks
(
    hex (3 4 8 7 17 18 22 21) ({circumferential_cells} {circum} 1) simpleGrading (4 2 1)
    hex (7 8 1 2 21 22 15 16) ({circumferential_cells} {circum} 1) simpleGrading (4 1 1)
    hex (4 5 9 8 18 19 23 22) ({join_cells} {circum} 1) simpleGrading (2 2 1)
    hex (5 6 10 9 19 20 24 23) ({outlet_cells} {circum} 1) simpleGrading (4 2 1)
    hex (8 11 0 1 22 25 14 15) ({circumferential_cells} {circum} 1) simpleGrading (4 1 1)
    hex (8 9 12 11 22 23 26 25) ({join_cells} {circumferential_cells} 1) simpleGrading (2 4 1)
    hex (9 10 13 12 23 24 27 26) ({outlet_cells} {circumferential_cells} 1) simpleGrading (4 4 1)
);
edges
(
    arc 3 7 ({_number(diagonal)} {_number(diagonal)} {_number(z_min)})
    arc 7 2 ({_number(-diagonal)} {_number(diagonal)} {_number(z_min)})
    arc 17 21 ({_number(diagonal)} {_number(diagonal)} {_number(z_max)})
    arc 21 16 ({_number(-diagonal)} {_number(diagonal)} {_number(z_max)})
    arc 4 8 ({_number(layer_diagonal)} {_number(layer_diagonal)} {_number(z_min)})
    arc 8 1 ({_number(-layer_diagonal)} {_number(layer_diagonal)} {_number(z_min)})
    arc 18 22 ({_number(layer_diagonal)} {_number(layer_diagonal)} {_number(z_max)})
    arc 22 15 ({_number(-layer_diagonal)} {_number(layer_diagonal)} {_number(z_max)})
);
boundary
(
    inlet {{ type patch; faces ((0 11 25 14) (11 12 26 25) (12 13 27 26)); }}
    outlet {{ type patch; faces ((6 10 24 20) (10 13 27 24)); }}
    cylinder {{ type wall; faces ((3 7 21 17) (7 2 16 21)); }}
    mirrorPlane
    {{
        type symmetryPlane;
        faces ((3 4 18 17) (1 15 16 2) (4 5 19 18) (5 6 20 19) (1 0 14 15));
    }}
    frontAndBack
    {{
        type empty;
        faces
        (
            (3 4 8 7) (7 8 1 2) (4 5 9 8) (5 6 10 9)
            (8 11 0 1) (8 9 12 11) (9 10 13 12)
            (17 21 22 18) (21 16 15 22) (18 22 23 19) (19 23 24 20)
            (22 15 14 25) (22 25 26 23) (23 26 27 24)
        );
    }}
);
mergePatchPairs ();
"""


def _cylinder_minimum_cell_size(
    *,
    radius: float,
    upstream: float,
    downstream: float,
    transverse: float,
    thickness: float,
    radial_cells: int,
    wake_cells: int,
) -> float:
    diameter = 2.0 * radius
    layer = 2.0 * diameter
    x_join = min(0.4 * downstream, downstream - diameter)
    y_join = (layer + transverse) / 2.0
    circum = max(4, radial_cells // 2)
    join_cells = max(1, wake_cells // 3)
    outlet_cells = wake_cells - join_cells
    candidates = (
        _smallest_graded_cell(layer - radius, radial_cells, 4.0),
        _smallest_graded_cell(upstream - layer, radial_cells, 4.0),
        _smallest_graded_cell(transverse - layer, radial_cells, 4.0),
        _smallest_graded_cell(x_join - layer, join_cells, 2.0),
        _smallest_graded_cell(downstream - x_join, outlet_cells, 4.0),
        _smallest_graded_cell(math.pi * radius / 2.0, circum, 2.0),
        _smallest_graded_cell(math.pi * layer / 2.0, circum, 2.0),
        _smallest_graded_cell(y_join - layer, circum, 2.0),
        _smallest_graded_cell(transverse - y_join, radial_cells, 4.0),
        thickness,
    )
    return min(candidates)


def _smallest_graded_cell(length: float, cells: int, expansion: float) -> float:
    if length <= 0.0 or cells <= 0 or expansion < 1.0:
        raise CompilationError("cylinder mesh grading has invalid dimensions")
    if cells == 1 or expansion == 1.0:
        return length / cells
    ratio = expansion ** (1.0 / (cells - 1))
    return length * (ratio - 1.0) / (ratio**cells - 1.0)


def _mirror_mesh_dict(diameter: float) -> str:
    return _header("mirrorMeshDict") + f"""
planeType       pointAndNormal;

pointAndNormalDict
{{
    point       (0 0 0);
    normal      (0 1 0);
}}

planeTolerance  {_number(diameter * 1e-3)};
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
    extrusion_span: float,
) -> str:
    write_interval = max(delta_t, end_time / 100.0)
    adjust = "yes" if adjust_time_step else "no"
    reference_area = diameter * extrusion_span
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
        Aref {_number(reference_area)};
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


def _transient_fv_solution(
    tolerance: float, *, pressure_reference: bool = False
) -> str:
    residual = _number(tolerance)
    reference = "\n    pRefCell 0;\n    pRefValue 0;" if pressure_reference else ""
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
    pFinal
    {{
        $p;
        relTol 0;
    }}
    U
    {{
        solver smoothSolver;
        smoother symGaussSeidel;
        tolerance {residual};
        relTol 0.01;
    }}
    UFinal
    {{
        $U;
        relTol 0;
    }}
}}
PIMPLE
{{
    momentumPredictor yes;
    nOuterCorrectors 1;
    nCorrectors 2;
    nNonOrthogonalCorrectors 0;
    residualControl
    {{
        p {residual};
        U {residual};
    }}{reference}
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


def _cavity_control_dict(
    *, end_time: float, side: float, thickness: float, delta_t: float
) -> str:
    write_interval = end_time / 50.0
    quarter = side / 4.0
    half = side / 2.0
    three_quarters = side * 3.0 / 4.0
    probe_z = thickness / 2.0
    return _header("controlDict", location="system") + f"""
solver          incompressibleFluid;
startFrom       startTime;
startTime       0;
stopAt          endTime;
endTime         {_number(end_time)};
deltaT          {_number(delta_t)};
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
            ({_number(quarter)} {_number(half)} {_number(probe_z)})
            ({_number(half)} {_number(half)} {_number(probe_z)})
            ({_number(three_quarters)} {_number(half)} {_number(probe_z)})
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
