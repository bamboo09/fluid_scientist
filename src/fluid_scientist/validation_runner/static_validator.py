"""Static validator for compiled OpenFOAM 13 cases.

The :class:`OpenFOAMCaseStaticValidator` performs all checks that can be
done *without* running any OpenFOAM command -- it inspects the compiled
case files (a :class:`~fluid_scientist.compiler.compiler.CompiledCase`)
for structural correctness, patch consistency, solver compliance,
turbulence field dependencies, transient/steady compatibility, periodic
pair integrity, function-object validity, and security policy compliance.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field

from fluid_scientist.compiler.compiler import CompiledCase, CompiledCaseManifest
from fluid_scientist.platform.profile import PlatformProfile, TURBULENCE_DEPENDENCIES


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------


class ValidationResult(BaseModel):
    """The result of a validation check or a set of checks.

    Attributes:
        check_name: Name of the check or validator.
        passed: ``True`` if all checks passed.
        errors: List of error messages (blocking).
        warnings: List of warning messages (non-blocking).
    """

    model_config = ConfigDict(extra="forbid")

    check_name: str = ""
    passed: bool = True
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)
        self.passed = False

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)

    def merge(self, other: ValidationResult) -> None:
        """Merge another result into this one."""
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)
        if not other.passed:
            self.passed = False


# ---------------------------------------------------------------------------
# Known function object types
# ---------------------------------------------------------------------------

KNOWN_FUNCTION_OBJECT_TYPES: set[str] = {
    "forces",
    "forceCoeffs",
    "surfaceFieldValue",
    "volFieldValue",
    "fieldValueDelta",
    "wallShearStress",
    "probes",
    "fieldAverage",
    "Q",
    "Lambda2",
    "ensightWrite",
    "surfaces",
    "streamlines",
    "cuttingSurface",
    "components",
    "div",
    "grad",
    "mag",
    "minMax",
    "patchAverage",
    "patchFlowRate",
    "totalPressure",
    "uncorrected",
    "yPlus",
}


# ---------------------------------------------------------------------------
# Static validator
# ---------------------------------------------------------------------------


class OpenFOAMCaseStaticValidator:
    """Static validator for compiled OpenFOAM 13 cases.

    Performs the following checks without running any OpenFOAM command:

    1. Patch consistency across all ``0/`` field files.
    2. Solver and file structure compliance (``incompressibleFluid``
       requires ``physicalProperties`` + ``momentumTransport``, NOT
       ``transportProperties``).
    3. Turbulence field dependencies (e.g. ``kOmegaSST`` needs ``k``,
       ``omega``, ``nut``).
    4. Steady vs transient compatibility (spectrum / vortex shedding
       requires transient).
    5. Periodic boundary pair integrity.
    6. Function object validity (type exists, patch exists, Aref/lRef > 0).
    7. Security policy (no ``codeStream``, no ``libs``, no shell
       variables, no ``systemCall``).
    """

    def __init__(
        self,
        platform: PlatformProfile | None = None,
    ) -> None:
        self.platform = platform or PlatformProfile()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate(
        self,
        case: CompiledCase,
        manifest: CompiledCaseManifest | None = None,
    ) -> ValidationResult:
        """Run all static checks on a compiled case.

        Parameters:
            case: The compiled case to validate.
            manifest: Optional metadata about the compiled case.

        Returns:
            A :class:`ValidationResult` with all errors and warnings.
        """
        result = ValidationResult(check_name="static_validation")

        self._check_patch_consistency(case, result)
        self._check_solver_and_file_structure(case, result)
        self._check_turbulence_field_dependencies(case, manifest, result)
        self._check_steady_transient_compatibility(case, manifest, result)
        self._check_periodic_pairs(case, result)
        self._check_function_objects(case, result)
        self._check_security(case, result)

        return result

    # ------------------------------------------------------------------
    # Check 1: Patch consistency
    # ------------------------------------------------------------------

    def _check_patch_consistency(
        self, case: CompiledCase, result: ValidationResult
    ) -> None:
        """Ensure all 0/ field files have the same set of patches."""
        field_files = [p for p in case.files if p.startswith("0/")]
        if not field_files:
            result.add_error("No 0/ field files found")
            return

        patch_sets: dict[str, list[str]] = {}
        for fpath in field_files:
            content = case.get(fpath) or ""
            patches = self._extract_patches_from_field(content)
            patch_sets[fpath] = patches

        # Compare all patch sets
        reference_file = field_files[0]
        reference_patches = set(patch_sets[reference_file])

        for fpath, patches in patch_sets.items():
            current = set(patches)
            if current != reference_patches:
                missing = reference_patches - current
                extra = current - reference_patches
                if missing:
                    result.add_error(
                        f"Patch consistency: {fpath} is missing patches: "
                        f"{sorted(missing)}"
                    )
                if extra:
                    result.add_error(
                        f"Patch consistency: {fpath} has extra patches: "
                        f"{sorted(extra)}"
                    )

    # ------------------------------------------------------------------
    # Check 2: Solver and file structure
    # ------------------------------------------------------------------

    def _check_solver_and_file_structure(
        self, case: CompiledCase, result: ValidationResult
    ) -> None:
        """Check that the solver module and file structure are correct."""
        # Check controlDict exists
        cd = case.get("system/controlDict")
        if not cd:
            result.add_error("Missing required file: system/controlDict")
            return

        # Check for solver incompressibleFluid; in controlDict
        if "solver" not in cd:
            result.add_error(
                "controlDict is missing 'solver' keyword "
                "(required for foamRun syntax)"
            )
        elif "incompressibleFluid" not in cd and "fluid" not in cd:
            result.add_warning(
                "controlDict 'solver' is not 'incompressibleFluid' or 'fluid'"
            )

        # Check required constant/ files
        required_constant = [
            "constant/physicalProperties",
            "constant/momentumTransport",
        ]
        for fpath in required_constant:
            if not case.get(fpath):
                result.add_error(f"Missing required file: {fpath}")

        # Check forbidden files
        for fpath in self.platform.forbidden_files:
            if case.get(fpath):
                result.add_error(
                    f"Forbidden file present: {fpath} "
                    f"(not allowed in Foundation 13)"
                )

        # Check required system/ files
        required_system = ["system/fvSchemes", "system/fvSolution"]
        for fpath in required_system:
            if not case.get(fpath):
                result.add_error(f"Missing required file: {fpath}")

        # Check for blockMeshDict
        if not case.get("system/blockMeshDict"):
            result.add_warning("Missing system/blockMeshDict (mesh generation)")

    # ------------------------------------------------------------------
    # Check 3: Turbulence field dependencies
    # ------------------------------------------------------------------

    def _check_turbulence_field_dependencies(
        self,
        case: CompiledCase,
        manifest: CompiledCaseManifest | None,
        result: ValidationResult,
    ) -> None:
        """Check that turbulence model fields are present."""
        # Determine turbulence model
        turb_model = "laminar"
        if manifest:
            turb_model = manifest.turbulence_model

        # Also check momentumTransport for the model name
        mt = case.get("constant/momentumTransport") or ""
        if "kOmegaSST" in mt:
            turb_model = "kOmegaSST"
        elif "kEpsilon" in mt:
            turb_model = "kEpsilon"
        elif "SpalartAllmaras" in mt:
            turb_model = "SpalartAllmaras"
        elif "WALE" in mt:
            turb_model = "WALE"
        elif "Smagorinsky" in mt:
            turb_model = "Smagorinsky"

        dep = TURBULENCE_DEPENDENCIES.get(turb_model)
        if dep is None:
            result.add_warning(
                f"Unknown turbulence model: {turb_model}, "
                f"cannot check field dependencies"
            )
            return

        # Check that required field files exist
        available_fields: set[str] = set()
        for fpath in case.files:
            if fpath.startswith("0/"):
                field_name = fpath.split("/")[1]
                available_fields.add(field_name)

        missing = dep.missing_fields(available_fields)
        if missing:
            result.add_error(
                f"Turbulence model '{turb_model}' requires fields "
                f"that are missing: {missing}"
            )

    # ------------------------------------------------------------------
    # Check 4: Steady vs transient compatibility
    # ------------------------------------------------------------------

    def _check_steady_transient_compatibility(
        self,
        case: CompiledCase,
        manifest: CompiledCaseManifest | None,
        result: ValidationResult,
    ) -> None:
        """Check that transient-only observables are not used in steady cases."""
        # Determine time mode
        time_mode = "transient"
        if manifest:
            time_mode = manifest.time_mode

        # Check fvSchemes for ddtSchemes
        fs = case.get("system/fvSchemes") or ""
        if "steadyState" in fs and "ddtSchemes" in fs:
            time_mode = "steady"

        if time_mode != "steady":
            return

        # Check controlDict for transient-only function objects
        cd = case.get("system/controlDict") or ""

        transient_only_observables = {
            "frequency_spectrum": "probes",
            "vortex_identification": "Q",
            "field_average": "fieldAverage",
        }

        for obs_name, fo_type in transient_only_observables.items():
            if fo_type in cd:
                result.add_error(
                    f"Observable '{obs_name}' (function object '{fo_type}') "
                    f"requires transient simulation but case is steady"
                )

    # ------------------------------------------------------------------
    # Check 5: Periodic boundary pairs
    # ------------------------------------------------------------------

    def _check_periodic_pairs(
        self, case: CompiledCase, result: ValidationResult
    ) -> None:
        """Check that periodic (cyclic/cyclicAMI) boundaries are paired."""
        # Get patches from blockMeshDict
        bmd = case.get("system/blockMeshDict") or ""
        if not bmd:
            return

        patches = self._extract_patches_from_blockmesh(bmd)
        if not patches:
            return

        # Find cyclic patches
        cyclic_patches: list[str] = []
        for patch in patches:
            patch_section = self._extract_patch_section(bmd, patch)
            if patch_section and (
                "cyclic" in patch_section
            ):
                cyclic_patches.append(patch)

        if not cyclic_patches:
            return

        # Cyclic patches must come in pairs
        if len(cyclic_patches) % 2 != 0:
            result.add_error(
                f"Cyclic/periodic patches must come in pairs, "
                f"found odd number: {cyclic_patches}"
            )

        # Check that each field file has the cyclic type for these patches
        field_files = [p for p in case.files if p.startswith("0/")]
        for fpath in field_files:
            content = case.get(fpath) or ""
            for cp in cyclic_patches:
                if cp in content:
                    # Check the BC type is cyclic
                    pattern = rf"{cp}\s*\{{[^}}]*type\s+(\w+)"
                    m = re.search(pattern, content)
                    if m and "cyclic" not in m.group(1):
                        result.add_error(
                            f"Periodic patch '{cp}' in {fpath} has "
                            f"non-cyclic BC type: {m.group(1)}"
                        )

    # ------------------------------------------------------------------
    # Check 6: Function objects
    # ------------------------------------------------------------------

    def _check_function_objects(
        self, case: CompiledCase, result: ValidationResult
    ) -> None:
        """Check function object validity in controlDict."""
        cd = case.get("system/controlDict") or ""
        if not cd:
            return

        # Find the functions section
        func_match = re.search(r"functions\s*\{", cd)
        if not func_match:
            return

        start = func_match.end()
        depth = 1
        pos = start
        while depth > 0 and pos < len(cd):
            if cd[pos] == "{":
                depth += 1
            elif cd[pos] == "}":
                depth -= 1
            pos += 1

        func_section = cd[start:pos - 1]

        # Get available patches from blockMeshDict or 0/ files
        available_patches: set[str] = set()
        bmd = case.get("system/blockMeshDict") or ""
        if bmd:
            available_patches.update(self._extract_patches_from_blockmesh(bmd))
        for fpath in case.files:
            if fpath.startswith("0/"):
                content = case.get(fpath) or ""
                available_patches.update(self._extract_patches_from_field(content))

        # Find all function object sub-dictionaries
        # Each function object is: name { type ...; ... }
        fo_pattern = re.compile(
            r"(\w+)\s*\{(.*?)\}",
            re.DOTALL,
        )

        for m in fo_pattern.finditer(func_section):
            fo_name = m.group(1)
            fo_body = m.group(2)

            # Extract type
            type_match = re.search(r"type\s+(\w+)", fo_body)
            if not type_match:
                result.add_error(
                    f"Function object '{fo_name}' is missing 'type' keyword"
                )
                continue

            fo_type = type_match.group(1)

            # Check type is known
            if fo_type not in KNOWN_FUNCTION_OBJECT_TYPES:
                result.add_error(
                    f"Function object '{fo_name}' has unknown type: {fo_type}"
                )

            # Check patches if referenced
            patches_match = re.search(r"patches?\s*\(([^)]+)\)", fo_body)
            if patches_match:
                referenced_patches = re.findall(r"\w+", patches_match.group(1))
                for rp in referenced_patches:
                    if rp not in available_patches:
                        result.add_error(
                            f"Function object '{fo_name}' references "
                            f"patch '{rp}' which does not exist in the mesh"
                        )

            # Check single patch reference
            patch_match = re.search(r"patch\s+(\w+)", fo_body)
            if patch_match:
                rp = patch_match.group(1)
                if rp not in available_patches:
                    result.add_error(
                        f"Function object '{fo_name}' references "
                        f"patch '{rp}' which does not exist in the mesh"
                    )

            # Check Aref and lRef for forceCoeffs
            if fo_type == "forceCoeffs":
                aref_match = re.search(r"Aref\s+\[[^\]]*\]\s+([\d.eE+-]+)", fo_body)
                lref_match = re.search(r"lRef\s+\[[^\]]*\]\s+([\d.eE+-]+)", fo_body)

                if aref_match:
                    aref_val = float(aref_match.group(1))
                    if aref_val <= 0:
                        result.add_error(
                            f"forceCoeffs '{fo_name}': Aref must be > 0, "
                            f"got {aref_val}"
                        )
                else:
                    result.add_error(
                        f"forceCoeffs '{fo_name}': Aref is missing"
                    )

                if lref_match:
                    lref_val = float(lref_match.group(1))
                    if lref_val <= 0:
                        result.add_error(
                            f"forceCoeffs '{fo_name}': lRef must be > 0, "
                            f"got {lref_val}"
                        )
                else:
                    result.add_error(
                        f"forceCoeffs '{fo_name}': lRef is missing"
                    )

    # ------------------------------------------------------------------
    # Check 7: Security
    # ------------------------------------------------------------------

    def _check_security(
        self, case: CompiledCase, result: ValidationResult
    ) -> None:
        """Check all files for security policy violations."""
        policy = self.platform.security_policy

        for fpath, content in case.files.items():
            violations = policy.validate_dict_content(content)
            for v in violations:
                result.add_error(f"Security violation in {fpath}: {v}")

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_patches_from_field(content: str) -> list[str]:
        """Extract patch names from the boundaryField section of a 0/ file."""
        match = re.search(r"boundaryField\s*\{", content)
        if not match:
            return []

        start = match.end()
        depth = 1
        pos = start
        while depth > 0 and pos < len(content):
            if content[pos] == "{":
                depth += 1
            elif content[pos] == "}":
                depth -= 1
            pos += 1

        section = content[start:pos - 1]

        patches: list[str] = []
        # Match: identifier on its own line followed by { on next line
        for m in re.finditer(r"^\s*(\w+)\s*\n\s*\{", section, re.MULTILINE):
            name = m.group(1)
            if name not in patches:
                patches.append(name)
        # Match: identifier followed by { on same line
        for m in re.finditer(r"^\s*(\w+)\s*\{", section, re.MULTILINE):
            name = m.group(1)
            if name not in patches:
                patches.append(name)

        return patches

    @staticmethod
    def _extract_patches_from_blockmesh(content: str) -> list[str]:
        """Extract patch names from the boundary section of blockMeshDict."""
        match = re.search(r"boundary\s*\(", content)
        if not match:
            return []

        start = match.end()
        depth = 1
        pos = start
        while depth > 0 and pos < len(content):
            if content[pos] == "(":
                depth += 1
            elif content[pos] == ")":
                depth -= 1
            pos += 1

        section = content[start:pos - 1]

        patches: list[str] = []
        for m in re.finditer(r"^\s*(\w+)\s*\n\s*\{", section, re.MULTILINE):
            name = m.group(1)
            if name not in patches:
                patches.append(name)
        for m in re.finditer(r"^\s*(\w+)\s*\{", section, re.MULTILINE):
            name = m.group(1)
            if name not in patches:
                patches.append(name)

        return patches

    @staticmethod
    def _extract_patch_section(content: str, patch_name: str) -> str | None:
        """Extract the sub-dictionary for a specific patch from blockMeshDict."""
        pattern = rf"{patch_name}\s*\{{(.*?)\n\s*\}}"
        m = re.search(pattern, content, re.DOTALL)
        if m:
            return m.group(1)
        return None


# Alias for the spec-required name
StaticValidator = OpenFOAMCaseStaticValidator


__all__ = [
    "OpenFOAMCaseStaticValidator",
    "StaticValidator",
    "ValidationResult",
]
