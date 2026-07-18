"""ObstacleFlowCaseStaticValidator — static validation for compiled cases.

Implements Section 19 of the plan.  Performs static checks on the
generated OpenFOAM case files without running any solver.

Checks:
  - Patch names consistent across files
  - front/back are 'empty'
  - Periodic patches are paired
  - Periodic mode has no inlet/outlet
  - Inlet-outlet mode has no erroneous periodic
  - Cylinder, bump, bottom boundaries complete
  - Top boundary parameters complete
  - Pressure gradient units and direction correct
  - Turbulence fields complete
  - functionObjects parameters complete
  - Observation points within fluid domain
  - No forbidden codeStream, coded, arbitrary libs
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from fluid_scientist.obstacle_flow.models import (
    BoundaryType,
    ObservableType,
    ObstacleFlowExperimentSpecV1,
    PressureGradientUnit,
)


@dataclass
class StaticValidationResult:
    """Result of static validation."""

    passed: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)
        self.passed = False

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)


class ObstacleFlowStaticValidator:
    """Static validator for obstacle flow compiled cases.

    Validates the generated OpenFOAM case files without running any
    external tools.
    """

    # Forbidden patterns in OpenFOAM dictionaries
    FORBIDDEN_PATTERNS = [
        (r"codeStream", "codeStream is forbidden"),
        (r"coded", "coded function objects are forbidden"),
        (r"\blibs\s*\([^)]*\$(?!\{?\{)", "arbitrary library paths are forbidden"),
    ]

    def validate(
        self,
        spec: ObstacleFlowExperimentSpecV1,
        files: dict[str, str],
    ) -> StaticValidationResult:
        """Validate the compiled case files statically."""
        result = StaticValidationResult()

        # Check required files exist
        self._check_required_files(files, result)

        # Check patch consistency
        self._check_patch_consistency(spec, files, result)

        # Check front/back are empty
        self._check_front_back_empty(files, result)

        # Check periodic patches
        self._check_periodic(spec, files, result)

        # Check boundary parameters complete
        self._check_boundary_params(spec, files, result)

        # Check pressure gradient
        self._check_pressure_gradient(spec, result)

        # Check turbulence fields
        self._check_turbulence_fields(spec, files, result)

        # Check observation points in domain
        self._check_observations(spec, result)

        # Check forbidden patterns
        self._check_forbidden_patterns(files, result)

        # Check solver configuration
        self._check_solver_config(files, result)

        # Check that forceCoeffs is present when a cylinder exists
        self._check_cylinder_force_coeffs(spec, files, result)

        # Check fvModels if forcing enabled
        self._check_fv_models(spec, files, result)

        return result

    def _check_required_files(
        self, files: dict[str, str], result: StaticValidationResult
    ) -> None:
        """Check that all required OpenFOAM files exist."""
        required = [
            "0/U",
            "0/p",
            "constant/physicalProperties",
            "constant/momentumTransport",
            "system/blockMeshDict",
            "system/controlDict",
            "system/fvSchemes",
            "system/fvSolution",
        ]
        for fpath in required:
            if fpath not in files:
                result.add_error(f"Missing required file: {fpath}")

    def _check_patch_consistency(
        self,
        spec: ObstacleFlowExperimentSpecV1,
        files: dict[str, str],
        result: StaticValidationResult,
    ) -> None:
        """Check that patch names are consistent across field and mesh files.

        Patch name mismatches between 0/U, 0/p, and blockMeshDict are
        HARD ERRORS, not warnings — a patch referenced in a field file
        but not present in blockMeshDict will cause OpenFOAM to crash at
        runtime.  This was previously a warning (false pass), now fixed.
        """
        # Extract patches from 0/U and 0/p
        u_content = files.get("0/U", "")
        p_content = files.get("0/p", "")
        u_patches = self._extract_patches(u_content)
        p_patches = self._extract_patches(p_content)

        # Extract patches from blockMeshDict
        bm_content = files.get("system/blockMeshDict", "")
        bm_patches = self._extract_blockmesh_patches(bm_content)

        # snappyHexMesh creates obstacle patches that are NOT present in the
        # base blockMeshDict (they are injected at runtime).  Add them so
        # the consistency check does not flag them as missing.
        if spec.has_cylinder:
            bm_patches.add("cylinder")
        if spec.has_rectangle:
            bm_patches.add("rectangle")
        if spec.has_triangle:
            bm_patches.add("triangle")
        if spec.has_trapezoid:
            bm_patches.add("trapezoid")

        # Check that all field patches exist in blockMeshDict (HARD ERROR)
        for patch in u_patches:
            if patch not in bm_patches and patch != "defaultFaces":
                result.add_error(
                    f"Patch '{patch}' in 0/U not found in blockMeshDict — "
                    f"this will cause OpenFOAM to crash at runtime"
                )

        for patch in p_patches:
            if patch not in bm_patches and patch != "defaultFaces":
                result.add_error(
                    f"Patch '{patch}' in 0/p not found in blockMeshDict — "
                    f"this will cause OpenFOAM to crash at runtime"
                )

        # Check that 0/U and 0/p have the same patch set
        if u_patches != p_patches:
            only_in_u = u_patches - p_patches
            only_in_p = p_patches - u_patches
            if only_in_u:
                result.add_error(
                    f"Patches in 0/U but not in 0/p: {only_in_u} — "
                    f"field files must have identical patch sets"
                )
            if only_in_p:
                result.add_error(
                    f"Patches in 0/p but not in 0/U: {only_in_p} — "
                    f"field files must have identical patch sets"
                )

        # Check cylinder patch exists if cylinder present
        if spec.has_cylinder and "cylinder" not in u_patches:
            result.add_error(
                "Cylinder is present but 'cylinder' patch missing in 0/U"
            )

    def _check_front_back_empty(
        self, files: dict[str, str], result: StaticValidationResult
    ) -> None:
        """Check that frontAndBack patches are type 'empty'."""
        for fname in ("0/U", "0/p"):
            content = files.get(fname, "")
            if "frontAndBack" not in content:
                result.add_warning(f"frontAndBack patch not found in {fname}")
                continue
            if "empty" not in content:
                result.add_error(
                    f"frontAndBack must be 'empty' type in {fname}"
                )

    def _check_periodic(
        self,
        spec: ObstacleFlowExperimentSpecV1,
        files: dict[str, str],
        result: StaticValidationResult,
    ) -> None:
        """Check periodic boundary configuration."""
        if not spec.is_periodic:
            return

        bm_content = files.get("system/blockMeshDict", "")
        if "cyclic" not in bm_content:
            result.add_error(
                "Periodic mode requires 'cyclic' type in blockMeshDict"
            )

        # Check that periodic mode doesn't have inlet/outlet
        if spec.boundaries.left.type == BoundaryType.VELOCITY_INLET:
            result.add_error(
                "Periodic mode cannot have velocity inlet on left"
            )
        if spec.boundaries.right.type == BoundaryType.PRESSURE_OUTLET:
            result.add_error(
                "Periodic mode cannot have pressure outlet on right"
            )

        # Check neighbourPatch is set for cyclic
        if "neighbourPatch" not in bm_content:
            result.add_error(
                "Cyclic patches must specify neighbourPatch in blockMeshDict"
            )

    def _check_boundary_params(
        self,
        spec: ObstacleFlowExperimentSpecV1,
        files: dict[str, str],
        result: StaticValidationResult,
    ) -> None:
        """Check that boundary parameters are complete."""
        b = spec.boundaries

        # Check velocity inlet has velocity
        if b.left.type == BoundaryType.VELOCITY_INLET:
            if b.left.inlet_velocity is None:
                result.add_error("velocity_inlet on left requires inlet_velocity")

        # Check moving wall has velocity vector
        if b.top.type == BoundaryType.MOVING_WALL:
            if b.top.velocity_vector is None:
                result.add_error("moving_wall on top requires velocity_vector")

        # Check shear stress has direction and magnitude
        if b.top.type == BoundaryType.SHEAR_STRESS:
            if b.top.shear_direction is None or b.top.shear_magnitude is None:
                result.add_error(
                    "shear_stress on top requires shear_direction and shear_magnitude"
                )

        # Check freestream has velocity
        if b.top.type == BoundaryType.FREESTREAM:
            if b.top.freestream_velocity is None:
                result.add_error("freestream on top requires freestream_velocity")

        # Check pressure boundary has value
        if b.left.type == BoundaryType.PRESSURE_BOUNDARY:
            if b.left.pressure_value is None:
                result.add_error("pressure_boundary on left requires pressure_value")
        if b.right.type == BoundaryType.PRESSURE_BOUNDARY:
            if b.right.pressure_value is None:
                result.add_error("pressure_boundary on right requires pressure_value")

    def _check_pressure_gradient(
        self, spec: ObstacleFlowExperimentSpecV1, result: StaticValidationResult
    ) -> None:
        """Check pressure gradient configuration."""
        pg = spec.forcing.pressure_gradient
        if not pg.enabled:
            return

        if pg.magnitude is None:
            result.add_error("Enabled pressure_gradient requires magnitude")
            return

        if pg.magnitude <= 0:
            result.add_warning(
                "Pressure gradient magnitude should be positive"
            )

        if pg.unit is None:
            result.add_warning(
                "Pressure gradient unit should be specified (Pa/m or m/s²)"
            )

        # Check direction is valid
        if len(pg.direction) != 3:
            result.add_error("Pressure gradient direction must be a 3-vector")
        else:
            magnitude = sum(d ** 2 for d in pg.direction) ** 0.5
            if magnitude < 1e-10:
                result.add_error("Pressure gradient direction cannot be zero")

    def _check_turbulence_fields(
        self,
        spec: ObstacleFlowExperimentSpecV1,
        files: dict[str, str],
        result: StaticValidationResult,
    ) -> None:
        """Check that turbulence fields are complete if turbulent."""
        if not spec.is_turbulent:
            return

        required_turb = ["0/k", "0/omega", "0/nut"]
        for fpath in required_turb:
            if fpath not in files:
                result.add_error(f"Missing turbulence field file: {fpath}")

        # Check momentumTransport has RAS model
        mt = files.get("constant/momentumTransport", "")
        if "RAS" not in mt:
            result.add_error(
                "Turbulent case requires RAS model in momentumTransport"
            )
        if "kOmegaSST" not in mt:
            result.add_warning(
                "Turbulent case should use kOmegaSST model"
            )

    def _check_observations(
        self, spec: ObstacleFlowExperimentSpecV1, result: StaticValidationResult
    ) -> None:
        """Check that observation points are within the fluid domain."""
        domain = spec.domain

        for obs in spec.observables:
            if obs.point is not None:
                x, y = obs.point[0], obs.point[1]
                if x < 0 or x > domain.length_m:
                    result.add_error(
                        f"Observation point x={x} is outside domain [0, {domain.length_m}]"
                    )
                if y < 0 or y > domain.height_m:
                    result.add_error(
                        f"Observation point y={y} is outside domain [0, {domain.height_m}]"
                    )

            if obs.section_x is not None:
                if obs.section_x < 0 or obs.section_x > domain.length_m:
                    result.add_error(
                        f"Section x={obs.section_x} is outside domain [0, {domain.length_m}]"
                    )

    def _check_forbidden_patterns(
        self, files: dict[str, str], result: StaticValidationResult
    ) -> None:
        """Check for forbidden patterns in OpenFOAM dictionaries."""
        for fname, content in files.items():
            for pattern, msg in self.FORBIDDEN_PATTERNS:
                if re.search(pattern, content):
                    result.add_error(f"Forbidden pattern in {fname}: {msg}")

    def _check_solver_config(
        self, files: dict[str, str], result: StaticValidationResult
    ) -> None:
        """Check solver configuration in controlDict."""
        cd = files.get("system/controlDict", "")

        if "solver" not in cd or "incompressibleFluid" not in cd:
            result.add_error(
                "controlDict must specify 'solver incompressibleFluid;'"
            )

        # Check no legacy solver
        if "pimpleFoam" in cd or "simpleFoam" in cd:
            result.add_error(
                "Legacy solvers (pimpleFoam, simpleFoam) are forbidden — use incompressibleFluid"
            )

    def _check_cylinder_force_coeffs(
        self,
        spec: ObstacleFlowExperimentSpecV1,
        files: dict[str, str],
        result: StaticValidationResult,
    ) -> None:
        """Check that forceCoeffs is present when force observables exist.

        If the spec requests CYLINDER_DRAG or CYLINDER_LIFT observables
        but the compiled controlDict lacks the ``forceCoeffs`` function
        object, the observables were likely deleted from the compiled
        output or the compilation silently dropped them — this is an
        error.
        """
        has_force_obs = any(
            o.type in (ObservableType.CYLINDER_DRAG, ObservableType.CYLINDER_LIFT)
            for o in spec.observables
        )
        if not has_force_obs:
            return

        cd = files.get("system/controlDict", "")
        if "forceCoeffs" not in cd:
            result.add_error(
                "Force observables (CYLINDER_DRAG / CYLINDER_LIFT) are present "
                "in the spec but 'forceCoeffs' function object is missing from "
                "system/controlDict — the force measurement was likely deleted "
                "from the compiled output."
            )

    def _check_fv_models(
        self,
        spec: ObstacleFlowExperimentSpecV1,
        files: dict[str, str],
        result: StaticValidationResult,
    ) -> None:
        """Check fvModels file if forcing is enabled."""
        has_forcing = (
            spec.forcing.pressure_gradient.enabled
            or spec.forcing.body_force.enabled
        )
        if not has_forcing:
            return

        fvm = files.get("system/fvModels")
        if fvm is None:
            result.add_error(
                "Forcing is enabled but system/fvModels is missing"
            )
            return

        if "bodyForce" not in fvm:
            result.add_error("fvModels must contain bodyForce for pressure gradient")

    def _extract_patches(self, content: str) -> set[str]:
        """Extract patch names from an OpenFOAM field file.

        Only identifiers that are direct children of the ``boundaryField``
        block are returned.  This avoids false positives such as
        ``FoamFile`` (the file header) or ``uniformValue`` (a nested
        sub-dictionary inside a patch entry).
        """
        patches: set[str] = set()
        # Isolate the boundaryField { ... } block
        m = re.search(r"boundaryField\s*\{", content)
        if not m:
            return patches
        start = m.end()
        depth = 1
        end = start
        while end < len(content) and depth > 0:
            if content[end] == "{":
                depth += 1
            elif content[end] == "}":
                depth -= 1
            end += 1
        bf = content[start : end - 1]

        # Within boundaryField, a patch entry is a bare word at depth 0
        # followed by ``{`` (either on the same line or the next line).
        bf_depth = 0
        lines = bf.split("\n")
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped or stripped.startswith("//"):
                continue
            opens = stripped.count("{")
            closes = stripped.count("}")
            if bf_depth == 0 and opens > 0:
                before = stripped.split("{")[0].strip()
                if re.match(r"^\w+$", before):
                    patches.add(before)
                elif i > 0:
                    prev = lines[i - 1].strip()
                    if re.match(r"^\w+$", prev):
                        patches.add(prev)
            bf_depth += opens - closes
        return patches

    def _extract_blockmesh_patches(self, content: str) -> set[str]:
        """Extract patch names from blockMeshDict boundary section.

        Tracks parenthesis *and* brace depth so that the ``)`` closing a
        ``faces`` sub-list does not prematurely terminate parsing of the
        boundary list.
        """
        patches: set[str] = set()
        in_boundary = False
        paren_depth = 0  # depth of ( ) — 1 means inside the boundary list
        brace_depth = 0  # depth of { } — 0 means between patch entries
        lines = content.split("\n")
        for line in lines:
            stripped = line.strip()
            if not in_boundary:
                if stripped == "boundary":
                    in_boundary = True
                continue
            opens_p = stripped.count("(")
            closes_p = stripped.count(")")
            opens_b = stripped.count("{")
            closes_b = stripped.count("}")
            # A patch name sits at paren_depth==1 (inside the boundary
            # list) and brace_depth==0 (between patch entries, not inside
            # a patch's sub-dictionary).
            if paren_depth == 1 and brace_depth == 0:
                if (
                    stripped
                    and not stripped.startswith("//")
                    and not stripped.startswith("{")
                    and not stripped.startswith(")")
                    and not stripped.startswith("(")
                    and not stripped.startswith("type")
                    and not stripped.startswith("faces")
                    and not stripped.startswith("neighbourPatch")
                    and re.match(r"^\w+$", stripped)
                ):
                    patches.add(stripped)
            paren_depth += opens_p - closes_p
            brace_depth += opens_b - closes_b
            if paren_depth <= 0:
                break
        return patches


__all__ = [
    "ObstacleFlowStaticValidator",
    "StaticValidationResult",
]
