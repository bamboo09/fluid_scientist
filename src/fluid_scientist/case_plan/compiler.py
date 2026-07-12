"""Native case compiler.

The :class:`NativeCaseCompiler` takes a :class:`CasePlan` and generates an
in-memory OpenFOAM case structure (a nested dict of dicts).  No actual
files are written to disk; the returned dict mirrors the standard
OpenFOAM directory layout (``system/``, ``constant/``, ``0/``).

The compiler validates that every required parameter is present in the
case plan before generating any output.  If a required parameter is
missing, a :class:`ValueError` with a specific message is raised.
"""

from __future__ import annotations

from typing import Any

from fluid_scientist.case_plan.models import (
    CasePlan,
    FunctionObjectSpec,
    MeasurementPlanSpec,
)

# ---------------------------------------------------------------------------
# Boundary condition type -> OpenFOAM patch mapping
# ---------------------------------------------------------------------------

# Map a boundary_condition_plan entry's "type" to velocity (U) and
# pressure (p) OpenFOAM boundary condition types.
_BC_TYPE_MAP: dict[str, dict[str, str]] = {
    "inlet": {"U": "fixedValue", "p": "zeroGradient"},
    "inlet_velocity": {"U": "fixedValue", "p": "zeroGradient"},
    "outlet": {"U": "zeroGradient", "p": "fixedValue"},
    "outlet_pressure": {"U": "zeroGradient", "p": "fixedValue"},
    "outlet_advective": {"U": "advective", "p": "advective"},
    "wall": {"U": "noSlip", "p": "zeroGradient"},
    "no_slip": {"U": "noSlip", "p": "zeroGradient"},
    "free_slip": {"U": "slip", "p": "zeroGradient"},
    "periodic": {"U": "cyclic", "p": "cyclic"},
}

# Map common patch names to blockMeshDict hex face vertex indices.
_PATCH_FACE_MAP: dict[str, list[int]] = {
    "inlet": [0, 4, 7, 3],
    "outlet": [1, 2, 6, 5],
    "wall": [0, 1, 5, 4],
    "walls": [0, 1, 5, 4],
    "bottom": [0, 1, 5, 4],
    "lowerwall": [0, 1, 5, 4],
    "top": [3, 7, 6, 2],
    "upperwall": [3, 7, 6, 2],
    "front": [0, 3, 2, 1],
    "frontandback": [0, 3, 2, 1],
    "back": [4, 5, 6, 7],
}


class NativeCaseCompiler:
    """Compile a :class:`CasePlan` into an in-memory OpenFOAM case structure.

    The compiler produces a nested dict mirroring the OpenFOAM directory
    layout::

        {
            "system": {"controlDict": ..., "fvSchemes": ..., ...},
            "constant": {"transportProperties": ..., ...},
            "0": {"U": ..., "p": ...},
        }

    All values are drawn from the :class:`CasePlan`.  Required parameters
    are validated up-front; if any is missing, a :class:`ValueError` with
    a specific message is raised.
    """

    def compile(self, case_plan: CasePlan) -> dict[str, Any]:
        """Compile ``case_plan`` to an in-memory OpenFOAM case structure.

        Raises:
            ValueError: if ``case_plan.can_compile`` is ``False`` or a
                required parameter is missing.
        """
        if not case_plan.can_compile:
            reasons = (
                "; ".join(case_plan.blocking_reasons)
                if case_plan.blocking_reasons
                else "blocking capabilities missing"
            )
            raise ValueError(f"case plan cannot be compiled: {reasons}")

        self._validate_required_parameters(case_plan)

        return {
            "system": {
                "controlDict": self._generate_control_dict(case_plan),
                "fvSchemes": self._generate_fv_schemes(case_plan),
                "fvSolution": self._generate_fv_solution(case_plan),
                "blockMeshDict": self._generate_block_mesh_dict(case_plan),
            },
            "constant": {
                "transportProperties": self._generate_transport_properties(
                    case_plan
                ),
                "turbulenceProperties": self._generate_turbulence_properties(
                    case_plan
                ),
            },
            "0": {
                "U": self._generate_velocity_field(case_plan),
                "p": self._generate_pressure_field(case_plan),
            },
        }

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate_required_parameters(self, case_plan: CasePlan) -> None:
        """Validate that all required parameters are present in the plan."""
        numerics = case_plan.numerics_plan
        for key in ("endTime", "deltaT"):
            if key not in numerics:
                raise ValueError(
                    f"Missing required parameter '{key}' in numerics_plan"
                )

        physics = case_plan.physical_model_plan
        nu = physics.get("nu", physics.get("kinematic_viscosity"))
        if nu is None:
            raise ValueError(
                "Missing required parameter 'nu' (or 'kinematic_viscosity') "
                "in physical_model_plan"
            )

        geometry = case_plan.geometry_plan
        for key in ("length", "height"):
            if key not in geometry:
                raise ValueError(
                    f"Missing required parameter '{key}' in geometry_plan"
                )

    # ------------------------------------------------------------------
    # system/controlDict
    # ------------------------------------------------------------------

    def _generate_control_dict(self, case_plan: CasePlan) -> dict[str, Any]:
        """Generate the controlDict dictionary.

        Uses 'solver incompressibleFluid;' instead of 'application pimpleFoam;'
        to comply with the workstation's security policy which requires exactly
        one literal incompressibleFluid solver entry. The workstation runs cases
        via 'foamRun -solver incompressibleFluid'.
        """
        numerics = case_plan.numerics_plan
        measurement = case_plan.measurement_plan

        control_dict: dict[str, Any] = {
            "solver": "incompressibleFluid",
            "startFrom": "latestTime",
            "startTime": 0,
            "stopAt": "endTime",
            "endTime": numerics["endTime"],
            "deltaT": numerics["deltaT"],
            "writeControl": numerics.get("writeControl", "timeStep"),
            "writeInterval": numerics.get(
                "writeInterval", measurement.write_interval
            ),
            "purgeWrite": 0,
            "writeFormat": "ascii",
            "writePrecision": 6,
            "writeCompression": "off",
            "timeFormat": "general",
            "timePrecision": 6,
            "runTimeModifiable": True,
        }

        # Include functionObjects from the measurement plan.
        functions = self._build_functions(measurement)
        if functions:
            control_dict["functions"] = functions

        return control_dict

    def _build_functions(
        self, measurement: MeasurementPlanSpec
    ) -> dict[str, dict[str, Any]]:
        """Build the ``functions`` block from the measurement plan.

        Function object IDs are sanitized to be valid OpenFOAM dictionary
        keywords (no spaces, special chars). Duplicate names are disambiguated.
        """
        functions: dict[str, dict[str, Any]] = {}
        used_names: set[str] = set()
        for fo in measurement.function_objects:
            # Sanitize: replace spaces and special chars with underscores
            raw_id = fo.function_object_id or "probe"
            safe_id = "".join(c if c.isalnum() or c == "_" else "_" for c in raw_id)
            safe_id = safe_id.strip("_")
            if not safe_id:
                safe_id = "probe"
            # Disambiguate duplicates
            final_id = safe_id
            counter = 2
            while final_id in used_names:
                final_id = f"{safe_id}_{counter}"
                counter += 1
            used_names.add(final_id)
            functions[final_id] = self._function_object_to_dict(
                fo, measurement.write_interval
            )
        return functions

    def _function_object_to_dict(
        self, fo: FunctionObjectSpec, write_interval: int
    ) -> dict[str, Any]:
        """Convert a :class:`FunctionObjectSpec` to an OpenFOAM dict fragment."""
        result: dict[str, Any] = {
            "type": fo.function_object_type,
            "writeControl": "timeStep",
            "writeInterval": write_interval,
        }

        # Library selection by type.
        # Note: 'libs' directive is omitted to avoid workstation sandbox
        # restrictions on dynamic code loading. OpenFOAM resolves most
        # function objects from the default library path automatically.
        if fo.patches:
            result["patches"] = list(fo.patches)
        if fo.fields:
            result["fields"] = list(fo.fields)
        if fo.output_directory:
            result["outputControl"] = "timeStep"
            result["outputInterval"] = write_interval

        # Merge type-specific configuration.
        # Skip empty probeLocations (causes OpenFOAM parse errors).
        for key, value in fo.configuration.items():
            if key == "probeLocations" and not value:
                continue  # Skip empty probe locations
            result.setdefault(key, value)

        return result

    # ------------------------------------------------------------------
    # system/fvSchemes
    # ------------------------------------------------------------------

    def _generate_fv_schemes(self, case_plan: CasePlan) -> dict[str, Any]:
        """Generate fvSchemes based on numerics_plan."""
        numerics = case_plan.numerics_plan
        steady = numerics.get("steady", False)

        # Allow the numerics_plan to override individual scheme entries.
        ddt_schemes = numerics.get(
            "ddtSchemes",
            {"default": "steadyState" if steady else "Euler"},
        )
        grad_schemes = numerics.get(
            "gradSchemes", {"default": "Gauss linear"}
        )
        div_schemes = numerics.get(
            "divSchemes",
            {"default": "Gauss linearUpwind grad(U)"},
        )
        laplacian_schemes = numerics.get(
            "laplacianSchemes",
            {"default": "Gauss linear corrected"},
        )
        interpolation_schemes = numerics.get(
            "interpolationSchemes", {"default": "linear"}
        )

        return {
            "ddtSchemes": ddt_schemes,
            "gradSchemes": grad_schemes,
            "divSchemes": div_schemes,
            "laplacianSchemes": laplacian_schemes,
            "interpolationSchemes": interpolation_schemes,
        }

    # ------------------------------------------------------------------
    # system/fvSolution
    # ------------------------------------------------------------------

    def _generate_fv_solution(self, case_plan: CasePlan) -> dict[str, Any]:
        """Generate fvSolution based on numerics_plan."""
        numerics = case_plan.numerics_plan
        steady = numerics.get("steady", False)

        solvers = numerics.get(
            "solvers",
            {
                "p": {
                    "solver": "GAMG",
                    "tolerance": 1e-06,
                    "relTol": 0.01,
                },
                "U": {
                    "solver": "smoothSolver",
                    "smoother": "symGaussSeidel",
                    "tolerance": 1e-08,
                    "relTol": 0.01,
                },
            },
        )

        result: dict[str, Any] = {"solvers": solvers}

        if steady:
            result["SIMPLE"] = numerics.get(
                "SIMPLE",
                {
                    "nNonOrthogonalCorrectors": 0,
                    "consistent": True,
                    "residualControl": {"p": 1e-5, "U": 1e-5},
                },
            )
        else:
            result["PIMPLE"] = numerics.get(
                "PIMPLE",
                {
                    "momentumPredictor": True,
                    "nOuterCorrectors": 1,
                    "nCorrectors": 2,
                    "nNonOrthogonalCorrectors": 0,
                },
            )

        relaxation = numerics.get(
            "relaxationFactors",
            {
                "equations": {"U": 0.9 if not steady else 0.7},
                "fields": {"p": 0.9 if not steady else 0.3},
            },
        )
        result["relaxationFactors"] = relaxation

        return result

    # ------------------------------------------------------------------
    # system/blockMeshDict
    # ------------------------------------------------------------------

    def _generate_block_mesh_dict(self, case_plan: CasePlan) -> str:
        """Generate blockMeshDict as a native OpenFOAM dictionary string."""
        geo = case_plan.geometry_plan
        mesh = case_plan.mesh_plan

        length = float(geo["length"])
        height = float(geo["height"])
        width = float(geo.get("width", 0.0))

        cells_x = int(mesh.get("cells_x", 50))
        cells_y = int(mesh.get("cells_y", 50))
        cells_z = int(mesh.get("cells_z", 1))

        # Build vertices list
        v = [
            (0.0, 0.0, 0.0),
            (length, 0.0, 0.0),
            (length, height, 0.0),
            (0.0, height, 0.0),
            (0.0, 0.0, width),
            (length, 0.0, width),
            (length, height, width),
            (0.0, height, width),
        ]
        vertices_str = "\n    ".join(f"({x} {y} {z})" for x, y, z in v)

        # Build boundary section
        boundary = self._generate_boundary(case_plan)
        boundary_lines = []
        for patch_name, patch_data in boundary.items():
            patch_type = patch_data.get("type", "patch")
            faces = patch_data.get("faces", [])
            if faces:
                # faces is a list of face definitions, each face is a list of vertex indices
                face_strs = []
                for face in faces:
                    if isinstance(face, list):
                        face_strs.append("(" + " ".join(str(v) for v in face) + ")")
                    else:
                        face_strs.append(str(face))
                faces_str = "\n            ".join(face_strs)
                boundary_lines.append(f"    {patch_name}\n    {{\n        type {patch_type};\n        faces\n        (\n            {faces_str}\n        );\n    }}")
            else:
                boundary_lines.append(f"    {patch_name}\n    {{\n        type {patch_type};\n        faces\n        (\n        );\n    }}")
        boundary_str = "\n".join(boundary_lines)

        return (
            f"vertices\n    (\n        {vertices_str}\n    );\n\n"
            f"blocks\n    (\n        hex (0 1 2 3 4 5 6 7) ({cells_x} {cells_y} {cells_z}) simpleGrading (1 1 1)\n    );\n\n"
            f"boundary\n    (\n{boundary_str}\n    );\n\n"
            f"defaultPatch\n    {{\n        type empty;\n    }};"
        )

    def _generate_boundary(self, case_plan: CasePlan) -> dict[str, Any]:
        """Generate the boundary section of blockMeshDict from BC plan."""
        boundary: dict[str, Any] = {}
        for patch_name, bc_data in case_plan.boundary_condition_plan.items():
            bc_type = bc_data.get("type", "")
            face_key = patch_name.lower()
            faces = _PATCH_FACE_MAP.get(face_key, [])
            patch_type = "patch"
            if bc_type in ("wall", "no_slip"):
                patch_type = "wall"
            elif bc_type == "periodic":
                patch_type = "cyclic"
            elif bc_type in ("front", "frontandback", "back") or face_key in (
                "front",
                "frontandback",
                "back",
            ):
                patch_type = "empty" if case_plan.dimensions == "2D" else "patch"
            boundary[patch_name] = {
                "type": patch_type,
                "faces": [faces] if faces else [],
            }
        return boundary

    # ------------------------------------------------------------------
    # constant/transportProperties
    # ------------------------------------------------------------------

    def _generate_transport_properties(self, case_plan: CasePlan) -> dict[str, Any]:
        """Generate transportProperties from physical_model_plan."""
        physics = case_plan.physical_model_plan
        nu = physics.get("nu", physics.get("kinematic_viscosity"))
        rho = physics.get("rho", physics.get("density", 1.0))

        return {
            "transportModel": "Newtonian",
            "nu": float(nu),
            "rho": float(rho),
        }

    # ------------------------------------------------------------------
    # constant/turbulenceProperties
    # ------------------------------------------------------------------

    def _generate_turbulence_properties(self, case_plan: CasePlan) -> dict[str, Any]:
        """Generate turbulenceProperties from physical_model_plan."""
        physics = case_plan.physical_model_plan
        turbulent = physics.get("turbulent", False)
        turbulence_model = physics.get("turbulence_model", "")

        if not turbulent:
            return {"simulationType": "laminar"}

        simulation_type = physics.get("simulation_type", "RAS")
        result: dict[str, Any] = {
            "simulationType": simulation_type,
        }
        if simulation_type == "RAS":
            model = turbulence_model or "kOmegaSST"
            result["RAS"] = {
                "model": model,
                "turbulence": True,
            }
        elif simulation_type == "LES":
            model = turbulence_model or "Smagorinsky"
            result["LES"] = {
                "model": model,
                "turbulence": True,
            }
        return result

    # ------------------------------------------------------------------
    # 0/U
    # ------------------------------------------------------------------

    def _generate_velocity_field(self, case_plan: CasePlan) -> dict[str, Any]:
        """Generate the 0/U field from initial and boundary condition plans."""
        ic = case_plan.initial_condition_plan
        velocity_ic = ic.get("velocity", {})
        if isinstance(velocity_ic, dict):
            vel_value = velocity_ic.get("value", [0.0, 0.0, 0.0])
        else:
            vel_value = [0.0, 0.0, 0.0]
        if isinstance(vel_value, (int, float)):
            vel_value = [float(vel_value), 0.0, 0.0]

        boundary_field: dict[str, Any] = {}
        for patch_name, bc_data in case_plan.boundary_condition_plan.items():
            boundary_field[patch_name] = self._velocity_boundary(bc_data)

        return {
            "dimensions": "[0 1 -1 0 0 0 0]",
            "internalField": {"uniform": list(vel_value)},
            "boundaryField": boundary_field,
        }

    def _velocity_boundary(self, bc_data: dict) -> dict[str, Any]:
        """Generate the U boundary condition for a single patch."""
        bc_type = bc_data.get("type", "")
        mapping = _BC_TYPE_MAP.get(bc_type, {"U": "zeroGradient", "p": "zeroGradient"})
        bc_name = mapping["U"]

        if bc_name == "fixedValue":
            velocity = bc_data.get("velocity", 0.0)
            return {
                "type": "fixedValue",
                "value": {"uniform": [float(velocity), 0.0, 0.0]},
            }
        if bc_name == "noSlip":
            return {"type": "noSlip"}
        if bc_name == "slip":
            return {"type": "slip"}
        if bc_name == "cyclic":
            return {"type": "cyclic"}
        if bc_name == "advective":
            return {"type": "advective"}
        return {"type": "zeroGradient"}

    # ------------------------------------------------------------------
    # 0/p
    # ------------------------------------------------------------------

    def _generate_pressure_field(self, case_plan: CasePlan) -> dict[str, Any]:
        """Generate the 0/p field from initial and boundary condition plans."""
        ic = case_plan.initial_condition_plan
        pressure_ic = ic.get("pressure", {})
        pressure_value = (
            pressure_ic.get("value", 0.0)
            if isinstance(pressure_ic, dict)
            else 0.0
        )

        boundary_field: dict[str, Any] = {}
        for patch_name, bc_data in case_plan.boundary_condition_plan.items():
            boundary_field[patch_name] = self._pressure_boundary(bc_data)

        return {
            "dimensions": "[0 2 -2 0 0 0 0]",
            "internalField": {"uniform": float(pressure_value)},
            "boundaryField": boundary_field,
        }

    def _pressure_boundary(self, bc_data: dict) -> dict[str, Any]:
        """Generate the p boundary condition for a single patch."""
        bc_type = bc_data.get("type", "")
        mapping = _BC_TYPE_MAP.get(bc_type, {"U": "zeroGradient", "p": "zeroGradient"})
        bc_name = mapping["p"]

        if bc_name == "fixedValue":
            pressure = bc_data.get("pressure", 0.0)
            return {
                "type": "fixedValue",
                "value": {"uniform": float(pressure)},
            }
        if bc_name == "cyclic":
            return {"type": "cyclic"}
        if bc_name == "advective":
            return {"type": "advective"}
        return {"type": "zeroGradient"}


__all__ = ["NativeCaseCompiler"]
