"""Deterministic OpenFOAM 13 component compiler.

The :class:`OpenFOAM13ComponentCompiler` takes a
:class:`~fluid_scientist.case_ir.models.ResolvedCaseIR`, a
:class:`~fluid_scientist.platform.profile.PlatformProfile`, and a
:class:`~fluid_scientist.components.registry.ComponentRegistry` and
produces a complete set of OpenFOAM dictionary files with full
provenance tracking via :class:`SourceMap`.

The compilation pipeline is::

    1. Select BasePack from composition_plan
    2. Merge BasePack templates
    3. Apply geometry components -> blockMeshDict
    4. Apply boundary components -> 0/U, 0/p, 0/k, ...
    5. Apply mesh components -> mesh strategy notes
    6. Apply observable components -> functionObjects in controlDict
    7. Generate physicalProperties from materials
    8. Generate momentumTransport from physics
    9. Generate fvSchemes, fvSolution from numerics
    10. Generate controlDict with `solver incompressibleFluid;`
    11. Track every value's source in SourceMap

All generated file content uses real Foundation 13 OpenFOAM dictionary
syntax, not placeholders.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from fluid_scientist.case_ir.models import RequestedCaseIR, ResolvedCaseIR
from fluid_scientist.components.base_packs import BasePack
from fluid_scientist.components.boundary_components import BoundaryComponent
from fluid_scientist.components.registry import ComponentRegistry
from fluid_scientist.compiler.source_map import SourceMap
from fluid_scientist.platform.profile import PlatformProfile

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FOAM_HEADER = """\
/*--------------------------------*- C++ -*----------------------------------*\\
  =========                 |
  \\\\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox
   \\\\    /   O peration     | Website:  https://openfoam.org
    \\\\  /    A nd           | Version:  13
     \\\\/     M anipulation  |
\\*---------------------------------------------------------------------------*/"""

# Field metadata: (field_class, dimensions, internal_field_default)
FIELD_META: dict[str, tuple[str, str, str]] = {
    "U": ("volVectorField", "[0 1 -1 0 0 0 0]", "uniform (0 0 0)"),
    "p": ("volScalarField", "[0 2 -2 0 0 0 0]", "uniform 0"),
    "k": ("volScalarField", "[0 2 -2 0 0 0 0]", "uniform 0.1"),
    "omega": ("volScalarField", "[0 0 -1 0 0 0 0]", "uniform 100"),
    "epsilon": ("volScalarField", "[0 2 -3 0 0 0 0]", "uniform 0.01"),
    "nuTilda": ("volScalarField", "[0 2 -1 0 0 0 0]", "uniform 0"),
    "nut": ("volScalarField", "[0 2 -1 0 0 0 0]", "uniform 0"),
}

# Default mapping from boundary component id to patch name
DEFAULT_PATCH_MAP: dict[str, str] = {
    "bc-uniform-velocity-inlet": "inlet",
    "bc-developed-pipe-inlet": "inlet",
    "bc-pressure-outlet": "outlet",
    "bc-convective-outlet": "outlet",
    "bc-no-slip-wall": "wall",
    "bc-slip-wall": "slipWall",
    "bc-moving-wall": "movingWall",
    "bc-symmetry-plane": "frontAndBack",
    "bc-periodic-pair": "periodic_a",
}


# ---------------------------------------------------------------------------
# Output models
# ---------------------------------------------------------------------------


class CompiledCase(BaseModel):
    """A compiled OpenFOAM case: a mapping of file path to content.

    Attributes:
        files: Dictionary mapping file paths (e.g. ``"system/controlDict"``)
            to their string content.
    """

    model_config = ConfigDict(extra="forbid")

    files: dict[str, str] = Field(default_factory=dict)

    def get(self, file_path: str) -> str | None:
        """Return the content of *file_path*, or ``None``."""
        return self.files.get(file_path)

    def set(self, file_path: str, content: str) -> None:
        """Set the content of *file_path*."""
        self.files[file_path] = content

    @property
    def file_paths(self) -> list[str]:
        """Return all file paths in sorted order."""
        return sorted(self.files.keys())


class CompiledCaseManifest(BaseModel):
    """Metadata describing a compiled case.

    Attributes:
        case_id: The case identifier.
        base_pack_id: The base pack used.
        solver_module: The foamRun solver module.
        application: The application (always ``foamRun``).
        time_mode: ``"steady"`` or ``"transient"``.
        turbulence_model: The turbulence model name.
        field_files: List of 0/ field file names.
        patch_names: List of mesh patch names.
        component_ids: All component ids used in compilation.
        system_files: List of system/ files generated.
        constant_files: List of constant/ files generated.
    """

    model_config = ConfigDict(extra="forbid")

    case_id: str = ""
    base_pack_id: str = ""
    solver_module: str = "incompressibleFluid"
    application: str = "foamRun"
    time_mode: str = "transient"
    turbulence_model: str = "laminar"
    field_files: list[str] = Field(default_factory=list)
    patch_names: list[str] = Field(default_factory=list)
    component_ids: list[str] = Field(default_factory=list)
    system_files: list[str] = Field(default_factory=list)
    constant_files: list[str] = Field(default_factory=list)


class ValidationPlan(BaseModel):
    """A plan specifying which validation stages to run.

    Attributes:
        stages: Ordered list of validation stage names.
        required_fields: Field files that must exist.
        required_patches: Patch names that must be present.
        security_checks: Security checks to perform.
    """

    model_config = ConfigDict(extra="forbid")

    stages: list[str] = Field(default_factory=list)
    required_fields: list[str] = Field(default_factory=list)
    required_patches: list[str] = Field(default_factory=list)
    security_checks: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Compiler
# ---------------------------------------------------------------------------


class OpenFOAM13ComponentCompiler:
    """Deterministic compiler from ResolvedCaseIR to OpenFOAM 13 files.

    Parameters:
        registry: The component registry for looking up components.
        platform: The platform profile (Foundation 13 locked).
    """

    def __init__(
        self,
        registry: ComponentRegistry,
        platform: PlatformProfile | None = None,
    ) -> None:
        self.registry = registry
        self.platform = platform or PlatformProfile()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compile(
        self,
        resolved: ResolvedCaseIR,
        requested: RequestedCaseIR | None = None,
    ) -> tuple[CompiledCase, CompiledCaseManifest, SourceMap, ValidationPlan]:
        """Compile a resolved case IR into OpenFOAM 13 dictionary files.

        Parameters:
            resolved: The resolved case IR with composition plan.
            requested: The original requested case IR (optional, provides
                entity and boundary-intent details).

        Returns:
            A tuple of ``(CompiledCase, CompiledCaseManifest, SourceMap,
            ValidationPlan)``.
        """
        case = CompiledCase()
        source_map = SourceMap()
        plan = resolved.composition_plan

        # Step 1: Select BasePack
        base_pack = self._select_base_pack(resolved, source_map)
        if base_pack is None:
            raise ValueError(
                f"Could not select a base pack. "
                f"composition_plan.base_pack='{plan.base_pack}'"
            )

        # Determine turbulence model
        turb_model = self._get_turbulence_model(resolved, base_pack, source_map)

        # Determine patches
        patches = self._determine_patches(resolved, requested, plan, source_map)

        # Determine fields
        fields = self._determine_fields(base_pack, turb_model, source_map)

        # Step 2: Merge BasePack templates and generate core files
        self._generate_physical_properties(
            case, source_map, resolved, requested, base_pack
        )
        self._generate_momentum_transport(
            case, source_map, resolved, base_pack, turb_model
        )
        self._generate_fv_schemes(case, source_map, base_pack, resolved)
        self._generate_fv_solution(case, source_map, base_pack, resolved)

        # Step 3: Apply geometry components -> blockMeshDict
        self._generate_block_mesh_dict(
            case, source_map, plan, requested, patches, base_pack
        )

        # Step 4: Apply boundary components -> 0/U, 0/p, etc.
        self._generate_field_files(
            case, source_map, plan, patches, fields, turb_model, requested
        )

        # Step 6: Apply observable components -> functionObjects
        function_objects = self._generate_function_objects(
            case, source_map, plan, requested, patches
        )

        # Step 10: Generate controlDict
        self._generate_control_dict(
            case, source_map, base_pack, resolved, function_objects
        )

        # Build manifest
        component_ids = (
            [base_pack.pack_id]
            + list(plan.geometry_components)
            + list(plan.boundary_components)
            + list(plan.mesh_components)
            + list(plan.observable_components)
        )
        manifest = CompiledCaseManifest(
            case_id=str(resolved.requested_case_ir_version),
            base_pack_id=base_pack.pack_id,
            solver_module=base_pack.solver_module,
            application=base_pack.application,
            time_mode=base_pack.time_mode,
            turbulence_model=turb_model,
            field_files=[f"0/{f}" for f in fields],
            patch_names=patches,
            component_ids=component_ids,
            system_files=[p for p in case.files if p.startswith("system/")],
            constant_files=[p for p in case.files if p.startswith("constant/")],
        )

        # Build validation plan
        validation_plan = ValidationPlan(
            stages=[
                "static",
                "dictionary",
                "mesh_build",
                "mesh_validate",
                "serial_smoke",
                "parallel_smoke",
            ],
            required_fields=fields,
            required_patches=patches,
            security_checks=[
                "no_codeStream",
                "no_libs",
                "no_shell_variables",
                "no_systemCall",
                "no_external_include",
            ],
        )

        return case, manifest, source_map, validation_plan

    # ------------------------------------------------------------------
    # Step 1: Select BasePack
    # ------------------------------------------------------------------

    def _select_base_pack(
        self, resolved: ResolvedCaseIR, source_map: SourceMap
    ) -> BasePack | None:
        plan = resolved.composition_plan
        if plan.base_pack:
            bp = self.registry.get_base_pack(plan.base_pack)
            if bp:
                source_map.add(
                    "system/controlDict",
                    "base_pack",
                    "/composition_plan/base_pack",
                    bp.pack_id,
                    "base_pack",
                )
                return bp
            raise ValueError(f"Base pack not found: {plan.base_pack}")

        # Auto-select based on resolved_physics
        physics = resolved.resolved_physics
        turbulence = physics.get("turbulence", "laminar")
        time_mode = physics.get("time_mode", "transient")

        if turbulence == "laminar" and time_mode == "transient":
            bp_id = "foundation13-incompressible-laminar-transient"
        elif turbulence == "RANS" and time_mode == "steady":
            bp_id = "foundation13-incompressible-rans-steady"
        elif turbulence == "RANS" and time_mode == "transient":
            bp_id = "foundation13-incompressible-rans-transient"
        elif turbulence == "LES" and time_mode == "transient":
            bp_id = "foundation13-incompressible-les-transient"
        else:
            bp_id = "foundation13-incompressible-laminar-transient"

        bp = self.registry.get_base_pack(bp_id)
        if bp:
            source_map.add(
                "system/controlDict",
                "base_pack",
                "/composition_plan/base_pack",
                bp.pack_id,
                "base_pack:auto_selected",
            )
        return bp

    # ------------------------------------------------------------------
    # Helper: get turbulence model
    # ------------------------------------------------------------------

    def _get_turbulence_model(
        self,
        resolved: ResolvedCaseIR,
        base_pack: BasePack,
        source_map: SourceMap,
    ) -> str:
        physics = resolved.resolved_physics
        turb = physics.get("turbulence", "laminar")
        if turb == "laminar":
            model = "laminar"
        elif turb == "RANS":
            model = physics.get("turbulence_model", base_pack.turbulence_support[0] if base_pack.turbulence_support else "kOmegaSST")
        elif turb == "LES":
            les_model = physics.get("turbulence_model", "LESWALE")
            if les_model == "LESSmagorinsky":
                model = "Smagorinsky"
            else:
                model = "WALE"
        else:
            model = "laminar"

        source_map.add(
            "constant/momentumTransport",
            "turbulence_model",
            "/resolved_physics/turbulence",
            model,
            "base_pack",
        )
        return model

    # ------------------------------------------------------------------
    # Helper: determine patches
    # ------------------------------------------------------------------

    def _determine_patches(
        self,
        resolved: ResolvedCaseIR,
        requested: RequestedCaseIR | None,
        plan: Any,
        source_map: SourceMap,
    ) -> list[str]:
        # If we have boundary intents with target_patch, use those
        if requested and requested.boundary_intents:
            patches: list[str] = []
            for bi in requested.boundary_intents:
                if bi.target_patch and bi.target_patch not in patches:
                    patches.append(bi.target_patch)
            if patches:
                source_map.add(
                    "system/blockMeshDict",
                    "patches",
                    "/boundary_intents/target_patch",
                    patches,
                    "case_ir",
                )
                return patches

        # Derive from boundary components
        patches = []
        for bc_id in plan.boundary_components:
            patch = DEFAULT_PATCH_MAP.get(bc_id, bc_id.replace("bc-", ""))
            if patch not in patches:
                patches.append(patch)
            # Periodic pair has two patches
            if bc_id == "bc-periodic-pair":
                if "periodic_b" not in patches:
                    patches.append("periodic_b")

        if not patches:
            patches = ["inlet", "outlet", "wall", "frontAndBack"]

        source_map.add(
            "system/blockMeshDict",
            "patches",
            "/composition_plan/boundary_components",
            patches,
            "compiler:default",
        )
        return patches

    # ------------------------------------------------------------------
    # Helper: determine fields
    # ------------------------------------------------------------------

    def _determine_fields(
        self,
        base_pack: BasePack,
        turb_model: str,
        source_map: SourceMap,
    ) -> list[str]:
        fields = ["U", "p"]
        if turb_model == "laminar":
            pass
        elif turb_model in ("kOmegaSST",):
            fields.extend(["k", "omega", "nut"])
        elif turb_model in ("kEpsilon",):
            fields.extend(["k", "epsilon", "nut"])
        elif turb_model in ("SpalartAllmaras",):
            fields.extend(["nuTilda", "nut"])
        elif turb_model in ("WALE", "Smagorinsky"):
            fields.extend(["nut"])

        # Deduplicate while preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for f in fields:
            if f not in seen:
                seen.add(f)
                unique.append(f)

        source_map.add(
            "0",
            "fields",
            "/resolved_physics/turbulence_model",
            unique,
            "base_pack",
        )
        return unique

    # ------------------------------------------------------------------
    # Step 7: Generate physicalProperties
    # ------------------------------------------------------------------

    def _generate_physical_properties(
        self,
        case: CompiledCase,
        source_map: SourceMap,
        resolved: ResolvedCaseIR,
        requested: RequestedCaseIR | None,
        base_pack: BasePack,
    ) -> None:
        file_path = "constant/physicalProperties"

        # Determine nu
        nu_value = "1e-06"
        nu_source = "/resolved_physics/nu"
        nu_component = "base_pack:default"

        if requested and requested.materials:
            for mat in requested.materials:
                if "kinematic_viscosity" in mat.properties:
                    pv = mat.properties["kinematic_viscosity"]
                    nu_value = str(pv.value)
                    nu_source = f"/materials/{mat.id}/kinematic_viscosity"
                    nu_component = "case_ir:materials"
                    break
                if "nu" in mat.properties:
                    pv = mat.properties["nu"]
                    nu_value = str(pv.value)
                    nu_source = f"/materials/{mat.id}/nu"
                    nu_component = "case_ir:materials"
                    break

        physics = resolved.resolved_physics
        if "nu" in physics:
            nu_value = str(physics["nu"])
            nu_source = "/resolved_physics/nu"
            nu_component = "resolved_physics"

        content = f"""{FOAM_HEADER}
FoamFile
{{
    version     2.0;
    format      ascii;
    class       dictionary;
    location    "constant";
    object      physicalProperties;
}}
// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

viscosityModel  constant;

nu              [0 2 -1 0 0 0 0] {nu_value};

// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //
"""

        case.set(file_path, content)
        source_map.add(
            file_path,
            "nu",
            nu_source,
            nu_value,
            nu_component,
        )
        source_map.add(
            file_path,
            "viscosityModel",
            "/base_pack/physical_properties_template",
            "constant",
            base_pack.pack_id,
        )

    # ------------------------------------------------------------------
    # Step 8: Generate momentumTransport
    # ------------------------------------------------------------------

    def _generate_momentum_transport(
        self,
        case: CompiledCase,
        source_map: SourceMap,
        resolved: ResolvedCaseIR,
        base_pack: BasePack,
        turb_model: str,
    ) -> None:
        file_path = "constant/momentumTransport"

        physics = resolved.resolved_physics
        turb = physics.get("turbulence", "laminar")

        if turb == "laminar":
            body = "simulationType laminar;"
        elif turb == "RANS":
            body = f"""simulationType RANS;
RAS
{{
    model           {turb_model};
    turbulence      on;
    printCoeffs     on;
}}"""
        elif turb == "LES":
            body = f"""simulationType LES;
LES
{{
    model           {turb_model};
    turbulence      on;
    printCoeffs     on;
    delta           cubeRootVol;
}}"""
        else:
            body = "simulationType laminar;"

        content = f"""{FOAM_HEADER}
FoamFile
{{
    version     2.0;
    format      ascii;
    class       dictionary;
    location    "constant";
    object      momentumTransport;
}}
// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

{body}

// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //
"""

        case.set(file_path, content)
        source_map.add(
            file_path,
            "simulationType",
            "/resolved_physics/turbulence",
            turb,
            base_pack.pack_id,
        )
        if turb != "laminar":
            source_map.add(
                file_path,
                "model",
                "/resolved_physics/turbulence_model",
                turb_model,
                base_pack.pack_id,
            )

    # ------------------------------------------------------------------
    # Step 9: Generate fvSchemes
    # ------------------------------------------------------------------

    def _generate_fv_schemes(
        self,
        case: CompiledCase,
        source_map: SourceMap,
        base_pack: BasePack,
        resolved: ResolvedCaseIR,
    ) -> None:
        file_path = "system/fvSchemes"
        tmpl = base_pack.fv_schemes_template

        ddt = tmpl.get("ddtSchemes", "default Euler;")
        grad = tmpl.get("gradSchemes", "default Gauss linear;")
        div = tmpl.get("divSchemes", "default none;")
        lap = tmpl.get("laplacianSchemes", "default Gauss linear corrected;")
        interp = tmpl.get("interpolationSchemes", "default linear;")
        sngrad = tmpl.get("snGradSchemes", "default corrected;")

        content = f"""{FOAM_HEADER}
FoamFile
{{
    version     2.0;
    format      ascii;
    class       dictionary;
    location    "system";
    object      fvSchemes;
}}
// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

ddtSchemes
{{
    {ddt}
}}

gradSchemes
{{
    {grad}
}}

divSchemes
{{
    {div}
}}

laplacianSchemes
{{
    {lap}
}}

interpolationSchemes
{{
    {interp}
}}

snGradSchemes
{{
    {sngrad}
}}

// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //
"""

        case.set(file_path, content)
        for key, val in tmpl.items():
            source_map.add(
                file_path,
                key,
                f"/base_pack/fv_schemes_template/{key}",
                val,
                base_pack.pack_id,
            )

    # ------------------------------------------------------------------
    # Step 9: Generate fvSolution
    # ------------------------------------------------------------------

    def _generate_fv_solution(
        self,
        case: CompiledCase,
        source_map: SourceMap,
        base_pack: BasePack,
        resolved: ResolvedCaseIR,
    ) -> None:
        file_path = "system/fvSolution"
        tmpl = base_pack.fv_solution_template

        solvers_str = tmpl.get("solvers", "")
        algorithm_str = ""
        relaxation_str = ""

        if base_pack.time_mode == "steady":
            algorithm_str = tmpl.get("SIMPLE", "")
            relaxation_str = tmpl.get("relaxationFactors", "")
        else:
            algorithm_str = tmpl.get("PIMPLE", "")

        sections = []
        sections.append(f"""solvers
{{
    {solvers_str}
}}""")

        if algorithm_str:
            if base_pack.time_mode == "steady":
                sections.append(f"""SIMPLE
{{
    {algorithm_str}
}}""")
            else:
                sections.append(f"""PIMPLE
{{
    {algorithm_str}
}}""")

        if relaxation_str:
            sections.append(f"""relaxationFactors
{{
    {relaxation_str}
}}""")

        body = "\n\n".join(sections)

        content = f"""{FOAM_HEADER}
FoamFile
{{
    version     2.0;
    format      ascii;
    class       dictionary;
    location    "system";
    object      fvSolution;
}}
// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

{body}

// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //
"""

        case.set(file_path, content)
        for key, val in tmpl.items():
            source_map.add(
                file_path,
                key,
                f"/base_pack/fv_solution_template/{key}",
                val,
                base_pack.pack_id,
            )

    # ------------------------------------------------------------------
    # Step 3: Generate blockMeshDict
    # ------------------------------------------------------------------

    def _generate_block_mesh_dict(
        self,
        case: CompiledCase,
        source_map: SourceMap,
        plan: Any,
        requested: RequestedCaseIR | None,
        patches: list[str],
        base_pack: BasePack,
    ) -> None:
        file_path = "system/blockMeshDict"

        # Default domain bounds
        min_x, min_y, min_z = 0.0, 0.0, 0.0
        max_x, max_y, max_z = 1.0, 0.1, 0.1

        # If we have entity data, extract domain bounds from box entity
        if requested and requested.entities:
            for ent in requested.entities:
                if ent.kind == "box":
                    if "length" in ent.parameters:
                        max_x = float(ent.parameters["length"].value)
                    if "height" in ent.parameters:
                        max_y = float(ent.parameters["height"].value)
                    if "width" in ent.parameters:
                        max_z = float(ent.parameters["width"].value)
                elif ent.kind == "pipe":
                    if "length" in ent.parameters:
                        max_x = float(ent.parameters["length"].value)
                    if "diameter" in ent.parameters:
                        dia = float(ent.parameters["diameter"].value)
                        max_y = dia
                        max_z = dia
                elif ent.kind == "cylinder":
                    if "length" in ent.parameters:
                        max_x = float(ent.parameters["length"].value)
                    if "diameter" in ent.parameters:
                        dia = float(ent.parameters["diameter"].value)
                        max_y = dia
                        max_z = dia

        # Default mesh resolution
        nx, ny, nz = 50, 20, 20

        # For 2D cases (small z extent), use 1 cell in z
        if max_z < 0.01:
            nz = 1

        # Check mesh components for resolution hints
        for mesh_id in plan.mesh_components:
            comp = self.registry.get_mesh(mesh_id)
            if comp and mesh_id == "mesh-block-mesh-basic":
                p = comp.parameters
                if "n_cells_x" in p:
                    nx = int(p["n_cells_x"]["default"])
                if "n_cells_y" in p:
                    ny = int(p["n_cells_y"]["default"])
                if "n_cells_z" in p:
                    nz = int(p["n_cells_z"]["default"])

        # Build boundary section from patches with CORRECT face assignments
        # Vertex numbering:
        #   0: (min_x, min_y, min_z)  bottom-left-front
        #   1: (max_x, min_y, min_z)  bottom-right-front
        #   2: (max_x, max_y, min_z)  top-right-front
        #   3: (min_x, max_y, min_z)  top-left-front
        #   4: (min_x, min_y, max_z)  bottom-left-back
        #   5: (max_x, min_y, max_z)  bottom-right-back
        #   6: (max_x, max_y, max_z)  top-right-back
        #   7: (min_x, max_y, max_z)  top-left-back
        #
        # Faces (OpenFOAM convention: right-hand rule, outward normal):
        #   inlet/left  (x=min):  0 3 7 4  (or 0 4 7 3 for reverse)
        #   outlet/right (x=max):  1 5 6 2  (or 1 2 6 5)
        #   bottom (y=min):  0 1 5 4
        #   top    (y=max):  3 7 6 2  (or 2 6 7 3)
        #   front  (z=min):  0 1 2 3
        #   back   (z=max):  4 5 6 7

        # Build boundary section
        boundary_entries = []
        # Check for periodic pairs
        periodic_pairs: dict[str, str] = {}
        if requested and requested.boundary_intents:
            for bi in requested.boundary_intents:
                if bi.semantic_role == "periodic":
                    # Match left-right or front-back pairs
                    if bi.target_patch == "left":
                        periodic_pairs["left"] = "right"
                    elif bi.target_patch == "right":
                        periodic_pairs["right"] = "left"
                    elif bi.target_patch == "front":
                        periodic_pairs["front"] = "back"
                    elif bi.target_patch == "back":
                        periodic_pairs["back"] = "front"

        for patch in patches:
            # Determine patch type
            if patch in ("inlet", "left"):
                if patch in periodic_pairs:
                    btype = "cyclic"
                    neighbour = periodic_pairs[patch]
                    faces = "(0 4 7 3)"
                    boundary_entries.append(
                        f"    {patch}\n    {{\n        type {btype};\n        neighbourPatch {neighbour};\n        faces\n        (\n            {faces}\n        );\n    }}"
                    )
                else:
                    faces = "(0 4 7 3)"
                    boundary_entries.append(
                        f"    {patch}\n    {{\n        type patch;\n        faces\n        (\n            {faces}\n        );\n    }}"
                    )
            elif patch in ("outlet", "right"):
                if patch in periodic_pairs:
                    btype = "cyclic"
                    neighbour = periodic_pairs[patch]
                    faces = "(1 2 6 5)"
                    boundary_entries.append(
                        f"    {patch}\n    {{\n        type {btype};\n        neighbourPatch {neighbour};\n        faces\n        (\n            {faces}\n        );\n    }}"
                    )
                else:
                    faces = "(1 2 6 5)"
                    boundary_entries.append(
                        f"    {patch}\n    {{\n        type patch;\n        faces\n        (\n            {faces}\n        );\n    }}"
                    )
            elif patch == "bottom":
                faces = "(0 1 5 4)"
                boundary_entries.append(
                    f"    {patch}\n    {{\n        type wall;\n        faces\n        (\n            {faces}\n        );\n    }}"
                )
            elif patch == "top":
                faces = "(3 7 6 2)"
                boundary_entries.append(
                    f"    {patch}\n    {{\n        type wall;\n        faces\n        (\n            {faces}\n        );\n    }}"
                )
            elif patch == "front":
                if patch in periodic_pairs:
                    btype = "cyclic"
                    neighbour = periodic_pairs[patch]
                    faces = "(0 3 2 1)"
                    boundary_entries.append(
                        f"    {patch}\n    {{\n        type {btype};\n        neighbourPatch {neighbour};\n        faces\n        (\n            {faces}\n        );\n    }}"
                    )
                else:
                    faces = "(0 3 2 1)"
                    boundary_entries.append(
                        f"    {patch}\n    {{\n        type empty;\n        faces\n        (\n            {faces}\n        );\n    }}"
                    )
            elif patch == "back":
                if patch in periodic_pairs:
                    btype = "cyclic"
                    neighbour = periodic_pairs[patch]
                    faces = "(4 5 6 7)"
                    boundary_entries.append(
                        f"    {patch}\n    {{\n        type {btype};\n        neighbourPatch {neighbour};\n        faces\n        (\n            {faces}\n        );\n    }}"
                    )
                else:
                    faces = "(4 5 6 7)"
                    boundary_entries.append(
                        f"    {patch}\n    {{\n        type empty;\n        faces\n        (\n            {faces}\n        );\n    }}"
                    )
            elif patch in ("wall", "topWall", "bottomWall", "movingWall"):
                # Generic wall — assign to bottom face by default
                faces = "(0 1 5 4)"
                boundary_entries.append(
                    f"    {patch}\n    {{\n        type wall;\n        faces\n        (\n            {faces}\n        );\n    }}"
                )
            elif patch == "frontAndBack":
                boundary_entries.append(
                    f"    front\n    {{\n        type empty;\n        faces\n        (\n            (0 3 2 1)\n        );\n    }}\n"
                    f"    back\n    {{\n        type empty;\n        faces\n        (\n            (4 5 6 7)\n        );\n    }}"
                )
            elif patch in ("periodic_a", "periodic_b"):
                neighbour = "periodic_b" if patch == "periodic_a" else "periodic_a"
                faces = "(0 4 7 3)" if patch == "periodic_a" else "(1 2 6 5)"
                boundary_entries.append(
                    f"    {patch}\n    {{\n        type cyclic;\n        neighbourPatch {neighbour};\n        faces\n        (\n            {faces}\n        );\n    }}"
                )
            else:
                # Default: assign to a face based on name
                faces = "(0 1 5 4)"
                boundary_entries.append(
                    f"    {patch}\n    {{\n        type patch;\n        faces\n        (\n            {faces}\n        );\n    }}"
                )

        boundary_block = "\n".join(boundary_entries)

        content = f"""{FOAM_HEADER}
FoamFile
{{
    version     2.0;
    format      ascii;
    class       dictionary;
    object      blockMeshDict;
}}
// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

scale   1;

vertices
(
    ({min_x} {min_y} {min_z})
    ({max_x} {min_y} {min_z})
    ({max_x} {max_y} {min_z})
    ({min_x} {max_y} {min_z})
    ({min_x} {min_y} {max_z})
    ({max_x} {min_y} {max_z})
    ({max_x} {max_y} {max_z})
    ({min_x} {max_y} {max_z})
);

blocks
(
    hex (0 1 2 3 4 5 6 7) ({nx} {ny} {nz}) simpleGrading (1 1 1)
);

edges
(
);

boundary
(
{boundary_block}
);

mergePatchPairs
(
);

// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //
"""

        case.set(file_path, content)
        source_map.add(
            file_path,
            "vertices",
            "/entities/parameters",
            f"({min_x} {min_y} {min_z})-({max_x} {max_y} {max_z})",
            "compiler:geometry",
        )
        source_map.add(
            file_path,
            "blocks",
            "/mesh_components/parameters",
            f"({nx} {ny} {nz})",
            "mesh-block-mesh-basic",
        )
        source_map.add(
            file_path,
            "boundary",
            "/boundary_components",
            patches,
            "compiler:boundary",
        )

    # ------------------------------------------------------------------
    # Step 4: Generate field files (0/U, 0/p, etc.)
    # ------------------------------------------------------------------

    def _generate_field_files(
        self,
        case: CompiledCase,
        source_map: SourceMap,
        plan: Any,
        patches: list[str],
        fields: list[str],
        turb_model: str,
        requested: RequestedCaseIR | None,
    ) -> None:
        # Build a mapping from patch -> boundary component
        patch_to_bc: dict[str, BoundaryComponent] = {}
        for bc_id in plan.boundary_components:
            bc = self.registry.get_boundary(bc_id)
            if bc:
                patch_name = DEFAULT_PATCH_MAP.get(bc_id, bc_id.replace("bc-", ""))
                patch_to_bc[patch_name] = bc
                if bc_id == "bc-periodic-pair":
                    patch_to_bc["periodic_b"] = bc

        # Build a mapping from patch -> semantic_role from boundary intents
        patch_to_role: dict[str, str] = {}
        if requested and requested.boundary_intents:
            for bi in requested.boundary_intents:
                if bi.target_patch:
                    patch_to_role[bi.target_patch] = bi.semantic_role

        # If we have boundary intents, map them to patches
        if requested and requested.boundary_intents:
            for bi in requested.boundary_intents:
                if bi.semantic_role:
                    # Try exact match first
                    bc = self.registry.find_boundary_by_role(bi.semantic_role)
                    if not bc:
                        # Try aliases
                        role_aliases = {
                            "periodic": "periodic_pair",
                            "no-slip": "no_slip_wall",
                            "noslip": "no_slip_wall",
                            "slip": "slip_wall",
                            "symmetry": "symmetry_plane",
                            "outlet": "pressure_outlet",
                            "inlet": "uniform_velocity_inlet",
                        }
                        aliased = role_aliases.get(bi.semantic_role)
                        if aliased:
                            bc = self.registry.find_boundary_by_role(aliased)
                    if bc and bi.target_patch:
                        patch_to_bc[bi.target_patch] = bc

        for field_name in fields:
            file_path = f"0/{field_name}"
            meta = FIELD_META.get(field_name, ("volScalarField", "[0 0 0 0 0 0 0]", "uniform 0"))
            field_class, dimensions, internal_field = meta

            # Build boundaryField
            bc_entries = []
            for patch in patches:
                role = patch_to_role.get(patch, "")
                bc = patch_to_bc.get(patch)

                # Handle periodic/cyclic patches directly
                if role in ("periodic", "periodic_pair") or (bc and bc.component_id == "bc-periodic-pair"):
                    if field_name == "U":
                        bc_entries.append(
                            f"    {patch}\n    {{\n        type            cyclic;\n    }}"
                        )
                    elif field_name == "p":
                        bc_entries.append(
                            f"    {patch}\n    {{\n        type            cyclic;\n    }}"
                        )
                    else:
                        bc_entries.append(
                            f"    {patch}\n    {{\n        type            cyclic;\n    }}"
                        )
                # Handle stress BC (slip for U, zeroGradient for p)
                elif role == "stress":
                    if field_name == "U":
                        bc_entries.append(
                            f"    {patch}\n    {{\n        type            slip;\n    }}"
                        )
                    else:
                        bc_entries.append(
                            f"    {patch}\n    {{\n        type            zeroGradient;\n    }}"
                        )
                # Handle no_slip_wall with Foundation 13 noSlip BC
                elif role in ("no_slip_wall", "no-slip", "noslip") or (bc and bc.component_id == "bc-no-slip-wall"):
                    if field_name == "U":
                        bc_entries.append(
                            f"    {patch}\n    {{\n        type            noSlip;\n    }}"
                        )
                    else:
                        bc_entries.append(
                            f"    {patch}\n    {{\n        type            zeroGradient;\n    }}"
                        )
                # Use component mapping if available
                elif bc and field_name in bc.foundation13_mapping:
                    mapping = bc.foundation13_mapping[field_name]
                    bc_type = mapping.get("type", "zeroGradient")
                    bc_value = mapping.get("value", "")
                    if bc_value:
                        bc_entries.append(
                            f"    {patch}\n    {{\n        type            {bc_type};\n        value           {bc_value};\n    }}"
                        )
                    else:
                        bc_entries.append(
                            f"    {patch}\n    {{\n        type            {bc_type};\n    }}"
                        )
                else:
                    # Default: zeroGradient
                    bc_entries.append(
                        f"    {patch}\n    {{\n        type            zeroGradient;\n    }}"
                    )

            boundary_field = "\n".join(bc_entries)

            content = f"""{FOAM_HEADER}
FoamFile
{{
    version     2.0;
    format      ascii;
    class       {field_class};
    object      {field_name};
}}
// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

dimensions      {dimensions};

internalField   {internal_field};

boundaryField
{{
{boundary_field}
}}

// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //
"""

            case.set(file_path, content)
            source_map.add(
                file_path,
                "dimensions",
                f"/fields/{field_name}/dimensions",
                dimensions,
                "compiler:field_meta",
            )
            source_map.add(
                file_path,
                "internalField",
                f"/fields/{field_name}/internal_field",
                internal_field,
                "compiler:field_meta",
            )
            for patch in patches:
                bc = patch_to_bc.get(patch)
                if bc and field_name in bc.foundation13_mapping:
                    source_map.add(
                        file_path,
                        f"boundaryField/{patch}/type",
                        f"/boundary_components/{bc.component_id}",
                        bc.foundation13_mapping[field_name].get("type", "zeroGradient"),
                        bc.component_id,
                    )

    # ------------------------------------------------------------------
    # Step 6: Generate function objects
    # ------------------------------------------------------------------

    def _generate_function_objects(
        self,
        case: CompiledCase,
        source_map: SourceMap,
        plan: Any,
        requested: RequestedCaseIR | None,
        patches: list[str],
    ) -> str:
        """Generate function objects block for controlDict.

        Returns the formatted ``functions { ... }`` block string.
        """
        entries: list[str] = []

        # Determine the "force patch" -- first wall-type patch
        force_patch = "wall"
        for p in patches:
            if p in ("wall", "topWall", "bottomWall", "cylinder", "sphere"):
                force_patch = p
                break
            if p not in ("inlet", "outlet", "frontAndBack", "symmetry"):
                force_patch = p
                break

        for obs_id in plan.observable_components:
            obs = self.registry.get_observable(obs_id)
            if not obs:
                continue

            # Build the function object sub-dictionary
            lines = [f"    {obs.component_id}"]
            lines.append("    {")
            for key, val in obs.foundation13_config_template.items():
                # Replace default patch names with actual patches
                if "cylinder" in val and force_patch != "cylinder":
                    val = val.replace("cylinder", force_patch)
                lines.append(f"        {key:<20} {val}")
            lines.append("    }")
            entries.append("\n".join(lines))

            source_map.add(
                "system/controlDict",
                f"functions/{obs.component_id}",
                f"/observable_components/{obs.component_id}",
                obs.function_object_type,
                obs.component_id,
            )

        if not entries:
            return ""

        return "functions\n{\n" + "\n".join(entries) + "\n}"

    # ------------------------------------------------------------------
    # Step 10: Generate controlDict
    # ------------------------------------------------------------------

    def _generate_control_dict(
        self,
        case: CompiledCase,
        source_map: SourceMap,
        base_pack: BasePack,
        resolved: ResolvedCaseIR,
        function_objects: str,
    ) -> None:
        file_path = "system/controlDict"
        tmpl = base_pack.control_dict_template

        # Build key-value lines from template
        lines: list[str] = []
        for key, val in tmpl.items():
            lines.append(f"{key:<20} {val}")

        control_body = "\n".join(lines)

        # Add function objects if any
        if function_objects:
            full_body = control_body + "\n\n" + function_objects
        else:
            full_body = control_body

        content = f"""{FOAM_HEADER}
FoamFile
{{
    version     2.0;
    format      ascii;
    class       dictionary;
    location    "system";
    object      controlDict;
}}
// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

{full_body}

// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //
"""

        case.set(file_path, content)
        for key, val in tmpl.items():
            source_map.add(
                file_path,
                key,
                f"/base_pack/control_dict_template/{key}",
                val,
                base_pack.pack_id,
            )


__all__ = [
    "CompiledCase",
    "CompiledCaseManifest",
    "OpenFOAM13ComponentCompiler",
    "ValidationPlan",
]
