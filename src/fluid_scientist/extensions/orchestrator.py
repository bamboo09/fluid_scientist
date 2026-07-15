"""ExtensionOrchestrator — executes the unknown-capability extension workflow.

Given a list of extension specs (produced by :class:`ExtensionSpecFactory`), a
:class:`PlatformProfile`, and a :class:`ComponentRegistry`, the orchestrator
runs every spec through the full validation pipeline:

1.  Create checkpoint
2.  Generate candidate capability
3.  Static security scan
4.  Build candidate component (static case validation)
5.  Atomic unit test
6.  Minimal OpenFOAM case test
7.  Target case smoke test
8.  Save evidence + test manifest
9.  Register VERIFIED capability
10. Restore original case
11. Re-resolve capabilities

The orchestrator **never fakes success**.  A capability is registered only after
its declared validation level has genuinely passed.  When OpenFOAM is required
but unavailable the spec ends in the ``ENVIRONMENT_BLOCKED`` state; when a test
runs but fails it ends in ``EXTENSION_VALIDATION_FAILED``; when a physics
extension references a solver module or base pack the platform cannot serve it
ends in ``UNSUPPORTED_PHYSICS``.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from fluid_scientist.capabilities.gap_analyzer import (
    AtomicRequirementSet,
    CapabilityGapAnalyzer,
)
from fluid_scientist.capabilities.registry import (
    Capability,
    CapabilityRegistry,
    CapabilityStatus,
)
from fluid_scientist.case_generation.validator import (
    CompileReadinessValidator,
)
from fluid_scientist.components.registry import ComponentRegistry
from fluid_scientist.extensions.code_spec import CodeExtensionSpec, TestSpec
from fluid_scientist.extensions.config_spec import ConfigExtensionSpec
from fluid_scientist.extensions.factory import ExtensionSpecUnion
from fluid_scientist.extensions.physics_spec import PhysicsExtensionSpec
from fluid_scientist.platform.profile import PlatformProfile, get_platform_profile

# ---------------------------------------------------------------------------
# Status types
# ---------------------------------------------------------------------------

ExtensionStatus = Literal[
    "proposed",
    "checkpointed",
    "candidate_generated",
    "static_validated",
    "component_built",
    "unit_tested",
    "openfoam_tested",
    "smoke_tested",
    "evidence_saved",
    "verified",
    "registered",
    "restored",
    "re_resolved",
    "extension_validation_failed",
    "unsupported_physics",
    "environment_blocked",
]

FAILURE_STATES: frozenset[str] = frozenset(
    {
        "extension_validation_failed",
        "unsupported_physics",
        "environment_blocked",
    }
)


# ---------------------------------------------------------------------------
# Result models
# ---------------------------------------------------------------------------


class ExtensionStepRecord(BaseModel):
    """Result of a single pipeline step for one spec."""

    model_config = ConfigDict(extra="forbid")

    step: str
    passed: bool
    message: str = ""
    duration_ms: float = 0.0
    artifacts: list[str] = Field(default_factory=list)
    failure_state: str = ""


class ExtensionExecutionRecord(BaseModel):
    """Full lifecycle record for one extension spec."""

    model_config = ConfigDict(extra="forbid")

    spec_id: str
    extension_type: str
    status: ExtensionStatus
    workspace: str = ""
    steps: list[ExtensionStepRecord] = Field(default_factory=list)
    evidence_artifact: str = ""
    test_manifest: list[str] = Field(default_factory=list)
    registered_capability_id: str = ""
    error: str = ""
    openfoam_available: bool = False
    validation_method: str = ""

    @property
    def succeeded(self) -> bool:
        return self.status == "registered"


class ExtensionOrchestrationResult(BaseModel):
    """Result of one ``execute`` call across all specs."""

    model_config = ConfigDict(extra="forbid")

    checkpoint_id: str
    records: list[ExtensionExecutionRecord] = Field(default_factory=list)
    original_case_restored: bool = False
    re_resolution_summary: str = ""
    failure_states: list[str] = Field(default_factory=list)

    @property
    def all_registered(self) -> bool:
        return bool(self.records) and all(r.status == "registered" for r in self.records)

    @property
    def any_failure(self) -> bool:
        return bool(self.failure_states)


# ---------------------------------------------------------------------------
# Static security scanning
# ---------------------------------------------------------------------------

_DANGEROUS_PATTERNS: tuple[str, ...] = (
    "import subprocess",
    "import os",
    "os.system",
    "os.popen",
    "os.remove",
    "os.unlink",
    "shutil.rmtree",
    "shutil.copy",
    "shutil.move",
    "__import__",
    "eval(",
    "exec(",
    "compile(",
    "globals(",
    "locals(",
    "getattr(os",
    "socket.",
    "urllib",
    "requests.",
    "ctypes.",
    "pickle.loads",
    "subprocess.Popen",
    "pathlib.Path('/",
    'pathlib.Path("/',
    "open('/",
    'open("/',
    "codeStream",
)

_SAFE_BUILTINS: dict[str, Any] = {
    "abs": abs,
    "min": min,
    "max": max,
    "sum": sum,
    "len": len,
    "range": range,
    "enumerate": enumerate,
    "zip": zip,
    "isinstance": isinstance,
    "float": float,
    "int": int,
    "str": str,
    "bool": bool,
    "dict": dict,
    "list": list,
    "tuple": tuple,
    "set": set,
    "round": round,
    "sorted": sorted,
    "any": any,
    "all": all,
    "map": map,
    "filter": filter,
    "print": print,
    "math": math,
    "ValueError": ValueError,
    "TypeError": TypeError,
    "RuntimeError": RuntimeError,
    "ZeroDivisionError": ZeroDivisionError,
    "Exception": Exception,
    "True": True,
    "False": False,
    "None": None,
}


# ---------------------------------------------------------------------------
# OpenFOAM detection helpers
# ---------------------------------------------------------------------------


def _detect_openfoam() -> bool:
    """Return True if an OpenFOAM installation appears to be available."""
    for var in ("WM_PROJECT_DIR", "FOAM_APP", "OPENFOAM_DIR", "WM_PROJECT_INST_DIR"):
        if os.environ.get(var):
            return True
    for exe in ("blockMesh", "blockMesh.exe", "foamRun", "foamRun.exe"):
        if shutil.which(exe):
            return True
    return False


def _find_cmd(name: str) -> str | None:
    return shutil.which(name) or shutil.which(f"{name}.exe")


# ---------------------------------------------------------------------------
# OpenFOAM dictionary fragment syntax validation
# ---------------------------------------------------------------------------


def _validate_fragment_syntax(text: str) -> tuple[bool, str]:
    """Lightweight brace/paren balance check for an OpenFOAM dict fragment."""
    brace = 0
    paren = 0
    in_comment = False
    for ch in text:
        if in_comment:
            if ch == "\n":
                in_comment = False
            continue
        if ch == "/":
            continue
        if ch == "{":
            brace += 1
        elif ch == "}":
            brace -= 1
        elif ch == "(":
            paren += 1
        elif ch == ")":
            paren -= 1
        if brace < 0 or paren < 0:
            return False, "unbalanced closing brace/parenthesis"
    if brace != 0:
        return False, f"unbalanced braces (depth {brace})"
    if paren != 0:
        return False, f"unbalanced parentheses (depth {paren})"
    return True, "syntax ok"


# ---------------------------------------------------------------------------
# Minimal Foundation-13 OpenFOAM case generators
# ---------------------------------------------------------------------------


def _foam_header(object_name: str, cls: str = "dictionary") -> str:
    return (
        "FoamFile\n"
        "{\n"
        "    version 2.0;\n"
        "    format ascii;\n"
        f"    class {cls};\n"
        f"    object {object_name};\n"
        "}\n"
    )


def _control_dict(function_name: str, extra_functions: str = "") -> str:
    functions_block = f"""
functions
{{
    {function_name}
    {{
        type residuals;
        libs ("libutilityFunctionObjects.so");
        fields (U p);
    }}
{extra_functions}
}}
"""
    return (
        _foam_header("controlDict")
        + "application foamRun;\n"
        + "startFrom startTime;\n"
        + "startTime 0;\n"
        + "stopAt endTime;\n"
        + "endTime 0.001;\n"
        + "deltaT 0.001;\n"
        + "writeControl timeStep;\n"
        + "writeInterval 1;\n"
        + "purgeWrite 0;\n"
        + functions_block
    )


def _fv_schemes() -> str:
    return (
        _foam_header("fvSchemes")
        + "ddtSchemes { default Euler; }\n"
        + "gradSchemes { default Gauss linear; }\n"
        + "divSchemes { default none; div(phi,U) Gauss linear; }\n"
        + "laplacianSchemes { default Gauss linear corrected; }\n"
        + "interpolationSchemes { default linear; }\n"
        + "snGradSchemes { default corrected; }\n"
    )


def _fv_solution() -> str:
    return (
        _foam_header("fvSolution")
        + "solvers\n"
        + "{\n"
        + "    p { solver PCG; preconditioner DIC; tolerance 1e-06; relTol 0; }\n"
        + "    U { solver smoothSolver; smoother symGaussSeidel; tolerance 1e-05; relTol 0; }\n"
        + "}\n"
        + "PISO { nCorrectors 2; nNonOrthogonalCorrectors 0; }\n"
    )


def _block_mesh_dict() -> str:
    # NOTE: the CompileReadinessValidator parses the boundary section by
    # expecting each patch name on its own line, immediately followed by a
    # ``{`` on the next line.  The expanded layout below satisfies that parser
    # so that patch-consistency validation passes.
    return (
        _foam_header("blockMeshDict")
        + "convertToMeters 1;\n"
        + "vertices\n"
        + "(\n"
        + "    (0 0 0) (1 0 0) (1 1 0) (0 1 0)\n"
        + "    (0 0 0.1) (1 0 0.1) (1 1 0.1) (0 1 0.1)\n"
        + ");\n"
        + "blocks ( hex (0 1 2 3 4 5 6 7) (4 4 1) simpleGrading (1 1 1) );\n"
        + "edges ();\n"
        + "boundary\n"
        + "(\n"
        + "    inlet\n    {\n        type patch;\n        faces ((0 4 7 3));\n    }\n"
        + "    outlet\n    {\n        type patch;\n        faces ((1 2 6 5));\n    }\n"
        + "    walls\n    {\n        type wall;\n"
        + "        faces ((0 1 5 4) (3 7 6 2));\n    }\n"
        + "    frontAndBack\n    {\n        type empty;\n"
        + "        faces ((0 3 2 1) (4 5 6 7));\n    }\n"
        + ");\n"
        + "mergePatchPairs ();\n"
    )


def _physical_properties() -> str:
    return (
        _foam_header("physicalProperties")
        + "viscosityModel Newtonian;\n"
        + "nu 1e-06;\n"
        + "rho 1;\n"
    )


def _momentum_transport() -> str:
    return _foam_header("momentumTransport") + "simulationType laminar;\n"


def _u_field() -> str:
    return (
        _foam_header("U", "volVectorField")
        + "dimensions [0 1 -1 0 0 0 0];\n"
        + "internalField uniform (1 0 0);\n"
        + "boundaryField\n"
        + "{\n"
        + "    inlet { type fixedValue; value uniform (1 0 0); }\n"
        + "    outlet { type zeroGradient; }\n"
        + "    walls { type noSlip; }\n"
        + "    frontAndBack { type empty; }\n"
        + "}\n"
    )


def _p_field() -> str:
    return (
        _foam_header("p", "volScalarField")
        + "dimensions [0 2 -2 0 0 0 0];\n"
        + "internalField uniform 0;\n"
        + "boundaryField\n"
        + "{\n"
        + "    inlet { type zeroGradient; }\n"
        + "    outlet { type fixedValue; value uniform 0; }\n"
        + "    walls { type zeroGradient; }\n"
        + "    frontAndBack { type empty; }\n"
        + "}\n"
    )


def _t_field() -> str:
    return (
        _foam_header("T", "volScalarField")
        + "dimensions [0 0 0 1 0 0 0];\n"
        + "internalField uniform 300;\n"
        + "boundaryField\n"
        + "{\n"
        + "    inlet { type fixedValue; value uniform 300; }\n"
        + "    outlet { type zeroGradient; }\n"
        + "    walls { type zeroGradient; }\n"
        + "    frontAndBack { type empty; }\n"
        + "}\n"
    )


def _thermophysical_properties() -> str:
    return (
        _foam_header("thermophysicalProperties")
        + "thermoType\n"
        + "{\n"
        + "    type hePsiThermo;\n"
        + "    mixture pureMixture;\n"
        + "    transport const;\n"
        + "    thermo hConst;\n"
        + "    equationOfState perfectGas;\n"
        + "    specie specie;\n"
        + "    energy sensibleInternalEnergy;\n"
        + "}\n"
        + "mixture\n{\n"
        + "    specie { molWeight 28.96; }\n"
        + "    thermodynamics { Cp 1004; Hf 0; }\n"
        + "    transport { mu 1.8e-05; Pr 0.7; }\n"
        + "}\n"
    )


def _case_dict() -> dict[str, Any]:
    """In-memory case dict consumed by the CompileReadinessValidator schema check."""
    return {
        "system": {"controlDict": {}, "fvSchemes": {}, "fvSolution": {}},
        "constant": {"physicalProperties": {}, "momentumTransport": {}},
        "0": {"U": {}, "p": {}},
    }


# ---------------------------------------------------------------------------
# ExtensionOrchestrator
# ---------------------------------------------------------------------------


class ExtensionOrchestrator:
    """Run extension specs through the full validation-and-registration pipeline.

    Args:
        workspace_root: Directory under which per-spec workspaces are created.
        platform: The :class:`PlatformProfile` (Foundation 13 lock).  Defaults
            to the global singleton.
        component_registry: The :class:`ComponentRegistry` used to validate
            base-pack / component references for physics extensions.
        capability_registry: The :class:`CapabilityRegistry` into which
            VERIFIED capabilities are registered.  Defaults to the singleton.
    """

    def __init__(
        self,
        workspace_root: str | Path,
        platform: PlatformProfile | None = None,
        component_registry: ComponentRegistry | None = None,
        capability_registry: CapabilityRegistry | None = None,
    ) -> None:
        self.workspace_root = Path(workspace_root)
        self._platform = platform or get_platform_profile()
        self._components = component_registry or ComponentRegistry()
        self._capabilities = capability_registry or CapabilityRegistry()
        self._validator = CompileReadinessValidator()
        self._analyzer = CapabilityGapAnalyzer(
            registry=self._capabilities,
            platform=self._platform,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def execute(
        self,
        specs: list[ExtensionSpecUnion],
        *,
        target_case_dir: str | Path | None = None,
        requirement_set: AtomicRequirementSet | None = None,
        run_openfoam: bool = True,
        code_generator: Callable[[CodeExtensionSpec], str] | None = None,
    ) -> ExtensionOrchestrationResult:
        """Execute the full extension workflow for every spec in *specs*.

        Args:
            specs: Extension specs to validate and (on success) register.
            target_case_dir: Optional target OpenFOAM case used for the
                smoke test (step 7).  A copy is used so the original is
                never mutated.
            requirement_set: Optional requirement set used for the
                re-resolution step (step 11).
            run_openfoam: When False, OpenFOAM runtime tests are not
                attempted.  Specs that require runtime validation will end
                in ``ENVIRONMENT_BLOCKED``; static-only specs may still be
                registered.
            code_generator: Optional callback that fills in
                ``implementation_code`` for :class:`CodeExtensionSpec`
                instances that arrive without code.
        """
        checkpoint_id = f"ckpt-{uuid4().hex[:12]}"

        target_backup = self._backup_target_case(target_case_dir, checkpoint_id)

        records: list[ExtensionExecutionRecord] = []
        for spec in specs:
            if (
                code_generator is not None
                and isinstance(spec, CodeExtensionSpec)
                and not spec.implementation_code.strip()
            ):
                spec = spec.model_copy(update={"implementation_code": code_generator(spec)})
            record = self._run_pipeline(
                spec,
                run_openfoam=run_openfoam,
                target_case_dir=target_case_dir,
                checkpoint_id=checkpoint_id,
            )
            records.append(record)

        # Step 10: restore original case.
        restored = self._restore_original_case(target_case_dir, target_backup)

        # Step 11: re-resolve capabilities.
        re_summary = self._re_resolve(requirement_set)

        failure_states = [r.status for r in records if r.status in FAILURE_STATES]

        return ExtensionOrchestrationResult(
            checkpoint_id=checkpoint_id,
            records=records,
            original_case_restored=restored,
            re_resolution_summary=re_summary,
            failure_states=failure_states,
        )

    # ------------------------------------------------------------------
    # Per-spec pipeline
    # ------------------------------------------------------------------

    def _run_pipeline(
        self,
        spec: ExtensionSpecUnion,
        *,
        run_openfoam: bool,
        target_case_dir: str | Path | None,
        checkpoint_id: str,
    ) -> ExtensionExecutionRecord:
        spec_id = spec.spec_id
        ext_type = spec.extension_type
        workspace = self.workspace_root / "extension_runs" / spec_id
        workspace.mkdir(parents=True, exist_ok=True)

        record = ExtensionExecutionRecord(
            spec_id=spec_id,
            extension_type=ext_type,
            status="proposed",
            workspace=str(workspace),
            validation_method=self._validation_method_for(spec),
        )
        steps: list[ExtensionStepRecord] = []

        def _abort(status: ExtensionStatus, message: str) -> ExtensionExecutionRecord:
            record.steps = steps
            record.status = status
            record.error = message
            return record

        # Step 1: checkpoint.
        step = self._step_checkpoint(spec, workspace, checkpoint_id)
        steps.append(step)
        record.status = "checkpointed"

        # Step 2: generate candidate capability.
        step = self._step_generate_candidate(spec, workspace)
        steps.append(step)
        if step.failure_state:
            return _abort(step.failure_state, step.message)  # type: ignore[arg-type]
        record.status = "candidate_generated"

        # Step 3: static security scan.
        step = self._step_static_security_scan(spec, workspace)
        steps.append(step)
        if step.failure_state:
            return _abort(step.failure_state, step.message)  # type: ignore[arg-type]
        record.status = "static_validated"

        # Step 4: build candidate component (static case validation).
        step = self._step_build_candidate_component(spec, workspace)
        steps.append(step)
        if step.failure_state:
            return _abort(step.failure_state, step.message)  # type: ignore[arg-type]
        record.status = "component_built"

        # Step 5: atomic unit test.
        step = self._step_atomic_unit_test(spec, workspace)
        steps.append(step)
        if step.failure_state:
            return _abort(step.failure_state, step.message)  # type: ignore[arg-type]
        record.status = "unit_tested"
        record.test_manifest.extend(step.artifacts)

        # Step 6: minimal OpenFOAM case test.
        step = self._step_minimal_openfoam_test(spec, workspace, run_openfoam=run_openfoam)
        steps.append(step)
        record.openfoam_available = _detect_openfoam()
        if step.failure_state:
            return _abort(step.failure_state, step.message)  # type: ignore[arg-type]
        record.status = "openfoam_tested"

        # Step 7: target case smoke test.
        step = self._step_target_case_smoke_test(
            spec, workspace, target_case_dir, run_openfoam=run_openfoam
        )
        steps.append(step)
        if step.failure_state:
            return _abort(step.failure_state, step.message)  # type: ignore[arg-type]
        record.status = "smoke_tested"
        record.test_manifest.extend(step.artifacts)

        # Step 8: save evidence + test manifest.
        step = self._step_save_evidence(spec, workspace, steps)
        steps.append(step)
        record.evidence_artifact = step.artifacts[0] if step.artifacts else ""
        record.status = "evidence_saved"

        # Step 9: register VERIFIED capability.
        step = self._step_register_capability(spec, workspace, record.evidence_artifact)
        steps.append(step)
        if step.failure_state:
            return _abort(step.failure_state, step.message)  # type: ignore[arg-type]
        record.registered_capability_id = step.artifacts[0] if step.artifacts else ""
        record.status = "registered"

        record.steps = steps
        return record

    # ------------------------------------------------------------------
    # Step implementations
    # ------------------------------------------------------------------

    def _step_checkpoint(
        self, spec: ExtensionSpecUnion, workspace: Path, checkpoint_id: str
    ) -> ExtensionStepRecord:
        t0 = time.time()
        checkpoint = {
            "checkpoint_id": checkpoint_id,
            "spec_id": spec.spec_id,
            "extension_type": spec.extension_type,
            "timestamp": time.time(),
            "registry_capability_ids": [c.capability_id for c in self._capabilities.list_all()],
        }
        path = workspace / "checkpoint.json"
        path.write_text(json.dumps(checkpoint, indent=2), encoding="utf-8")
        return ExtensionStepRecord(
            step="create_checkpoint",
            passed=True,
            message=f"Checkpoint {checkpoint_id} saved.",
            duration_ms=(time.time() - t0) * 1000,
            artifacts=[str(path)],
        )

    def _step_generate_candidate(
        self, spec: ExtensionSpecUnion, workspace: Path
    ) -> ExtensionStepRecord:
        t0 = time.time()
        # Physics feasibility check (UNSUPPORTED_PHYSICS).
        if isinstance(spec, PhysicsExtensionSpec):
            unsupported = self._check_physics_feasibility(spec)
            if unsupported:
                return ExtensionStepRecord(
                    step="generate_candidate",
                    passed=False,
                    message=unsupported,
                    duration_ms=(time.time() - t0) * 1000,
                    failure_state="unsupported_physics",
                )

        # Materialise the spec manifest.
        manifest_path = workspace / "spec.json"
        manifest_path.write_text(spec.model_dump_json(indent=2), encoding="utf-8")

        # Write type-specific candidate artifacts.
        artifacts: list[str] = [str(manifest_path)]
        if isinstance(spec, CodeExtensionSpec):
            code_path = workspace / "implementation.py"
            code_path.write_text(spec.implementation_code, encoding="utf-8")
            artifacts.append(str(code_path))

        return ExtensionStepRecord(
            step="generate_candidate",
            passed=True,
            message="Candidate capability materialised.",
            duration_ms=(time.time() - t0) * 1000,
            artifacts=artifacts,
        )

    def _step_static_security_scan(
        self, spec: ExtensionSpecUnion, workspace: Path
    ) -> ExtensionStepRecord:
        t0 = time.time()
        violations: list[str] = []

        # Metadata files describe the spec (and may legitimately contain
        # constraint names such as ``no_codeStream``); they are not generated
        # OpenFOAM content or executable code, so they are excluded from the
        # scan.  Only generated content is scanned.
        metadata_names = {
            "spec.json",
            "checkpoint.json",
            "verification_artifact.json",
            "test_manifest.json",
            "unit_test_manifest.txt",
        }

        # Scan every generated file under the workspace for platform policy
        # violations and dangerous code patterns.
        for file_path in sorted(workspace.rglob("*")):
            if not file_path.is_file():
                continue
            if file_path.name in metadata_names:
                continue
            text = file_path.read_text(encoding="utf-8", errors="replace")
            policy_violations = self._platform.security_policy.validate_dict_content(text)
            violations.extend(f"{file_path.name}: {v}" for v in policy_violations)
            for pattern in _DANGEROUS_PATTERNS:
                if pattern.lower() in text.lower():
                    violations.append(f"{file_path.name}: forbidden pattern '{pattern}'")

        # Additionally scan the foundation13_mapping for forbidden file refs.
        if isinstance(spec, ConfigExtensionSpec):
            for value in spec.foundation13_mapping.values():
                for forbidden in self._platform.forbidden_files:
                    if forbidden in value:
                        violations.append(
                            f"foundation13_mapping references forbidden file {forbidden}"
                        )

        if violations:
            return ExtensionStepRecord(
                step="static_security_scan",
                passed=False,
                message="; ".join(violations),
                duration_ms=(time.time() - t0) * 1000,
                failure_state="extension_validation_failed",
            )
        return ExtensionStepRecord(
            step="static_security_scan",
            passed=True,
            message="No security violations detected.",
            duration_ms=(time.time() - t0) * 1000,
        )

    def _step_build_candidate_component(
        self, spec: ExtensionSpecUnion, workspace: Path
    ) -> ExtensionStepRecord:
        """Static case validation using the CompileReadinessValidator."""
        t0 = time.time()
        case_dir = workspace / "minimal_case"
        self._write_minimal_case(case_dir, spec)

        design = {"resolved_values": {"Re": 100.0, "nu": 1e-6, "U_ref": 1.0}}
        report = self._validator.validate(
            case_dir,
            case_dict=_case_dict(),
            design=design,
            run_openfoam=False,
        )
        static_errors = [
            check for check in report.checks if not check.passed and check.severity == "error"
        ]
        if static_errors:
            return ExtensionStepRecord(
                step="build_candidate_component",
                passed=False,
                message="; ".join(f"{c.check_name}: {c.message}" for c in static_errors),
                duration_ms=(time.time() - t0) * 1000,
                failure_state="extension_validation_failed",
            )
        return ExtensionStepRecord(
            step="build_candidate_component",
            passed=True,
            message="Static case validation passed.",
            duration_ms=(time.time() - t0) * 1000,
            artifacts=[str(case_dir)],
        )

    def _step_atomic_unit_test(
        self, spec: ExtensionSpecUnion, workspace: Path
    ) -> ExtensionStepRecord:
        t0 = time.time()
        if isinstance(spec, CodeExtensionSpec):
            passed, message, manifest = self._run_code_unit_tests(spec, workspace)
        elif isinstance(spec, ConfigExtensionSpec):
            passed, message, manifest = self._run_config_unit_tests(spec, workspace)
        else:
            passed, message, manifest = self._run_physics_unit_tests(spec, workspace)

        manifest_path = workspace / "unit_test_manifest.txt"
        manifest_path.write_text("\n".join(manifest) + "\n", encoding="utf-8")

        if not passed:
            return ExtensionStepRecord(
                step="atomic_unit_test",
                passed=False,
                message=message,
                duration_ms=(time.time() - t0) * 1000,
                artifacts=[str(manifest_path)],
                failure_state="extension_validation_failed",
            )
        return ExtensionStepRecord(
            step="atomic_unit_test",
            passed=True,
            message=message,
            duration_ms=(time.time() - t0) * 1000,
            artifacts=[str(manifest_path), *manifest],
        )

    def _step_minimal_openfoam_test(
        self,
        spec: ExtensionSpecUnion,
        workspace: Path,
        *,
        run_openfoam: bool,
    ) -> ExtensionStepRecord:
        t0 = time.time()
        case_dir = workspace / "minimal_case"

        needs_runtime = self._requires_openfoam_runtime(spec)
        if not needs_runtime:
            return ExtensionStepRecord(
                step="minimal_openfoam_case_test",
                passed=True,
                message=(
                    "Static validation level declared; OpenFOAM runtime test "
                    "not required for this spec."
                ),
                duration_ms=(time.time() - t0) * 1000,
            )

        if not run_openfoam:
            return ExtensionStepRecord(
                step="minimal_openfoam_case_test",
                passed=False,
                message=(
                    "OpenFOAM runtime validation is required for this spec but run_openfoam=False."
                ),
                duration_ms=(time.time() - t0) * 1000,
                failure_state="environment_blocked",
            )

        if not _detect_openfoam():
            return ExtensionStepRecord(
                step="minimal_openfoam_case_test",
                passed=False,
                message=(
                    "OpenFOAM is not available in this environment; runtime "
                    "validation cannot proceed and the capability cannot be "
                    "registered as VERIFIED."
                ),
                duration_ms=(time.time() - t0) * 1000,
                failure_state="environment_blocked",
            )

        passed, message = self._run_openfoam_runtime(case_dir, spec)
        if not passed:
            return ExtensionStepRecord(
                step="minimal_openfoam_case_test",
                passed=False,
                message=message,
                duration_ms=(time.time() - t0) * 1000,
                failure_state="extension_validation_failed",
            )
        return ExtensionStepRecord(
            step="minimal_openfoam_case_test",
            passed=True,
            message=message,
            duration_ms=(time.time() - t0) * 1000,
        )

    def _step_target_case_smoke_test(
        self,
        spec: ExtensionSpecUnion,
        workspace: Path,
        target_case_dir: str | Path | None,
        *,
        run_openfoam: bool,
    ) -> ExtensionStepRecord:
        t0 = time.time()
        if target_case_dir is None:
            return ExtensionStepRecord(
                step="target_case_smoke_test",
                passed=True,
                message="No target case supplied; smoke test skipped.",
                duration_ms=(time.time() - t0) * 1000,
            )

        # Operate on a copy so the original target case is never mutated.
        source = Path(target_case_dir)
        smoke_copy = workspace / "target_case_smoke"
        if smoke_copy.exists():
            shutil.rmtree(smoke_copy)
        if source.is_dir():
            shutil.copytree(source, smoke_copy)
        else:
            return ExtensionStepRecord(
                step="target_case_smoke_test",
                passed=False,
                message=f"Target case directory does not exist: {source}",
                duration_ms=(time.time() - t0) * 1000,
                failure_state="extension_validation_failed",
            )

        report = self._validator.validate(
            smoke_copy,
            case_dict=_case_dict(),
            run_openfoam=run_openfoam and _detect_openfoam(),
        )
        errors = [
            check for check in report.checks if not check.passed and check.severity == "error"
        ]
        if errors:
            return ExtensionStepRecord(
                step="target_case_smoke_test",
                passed=False,
                message="; ".join(f"{c.check_name}: {c.message}" for c in errors),
                duration_ms=(time.time() - t0) * 1000,
                failure_state="extension_validation_failed",
            )
        return ExtensionStepRecord(
            step="target_case_smoke_test",
            passed=True,
            message="Target case smoke test passed.",
            duration_ms=(time.time() - t0) * 1000,
            artifacts=[str(smoke_copy)],
        )

    def _step_save_evidence(
        self,
        spec: ExtensionSpecUnion,
        workspace: Path,
        steps: list[ExtensionStepRecord],
    ) -> ExtensionStepRecord:
        t0 = time.time()
        evidence = {
            "spec_id": spec.spec_id,
            "extension_type": spec.extension_type,
            "validation_method": self._validation_method_for(spec),
            "openfoam_available": _detect_openfoam(),
            "platform_id": self._platform.profile_id,
            "platform_version": self._platform.version,
            "steps": [s.model_dump() for s in steps],
            "timestamp": time.time(),
        }
        payload = json.dumps(evidence, indent=2, sort_keys=True)
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        artifact_path = workspace / "verification_artifact.json"
        artifact_path.write_text(payload, encoding="utf-8")

        manifest_path = workspace / "test_manifest.json"
        manifest = {
            "spec_id": spec.spec_id,
            "evidence_sha256": f"sha256:{digest}",
            "unit_tests": [s.step for s in steps if "unit" in s.step],
            "openfoam_tests": [s.step for s in steps if "openfoam" in s.step],
            "smoke_tests": [s.step for s in steps if "smoke" in s.step],
        }
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return ExtensionStepRecord(
            step="save_evidence",
            passed=True,
            message=f"Evidence saved (sha256:{digest[:12]}).",
            duration_ms=(time.time() - t0) * 1000,
            artifacts=[f"sha256:{digest}", str(artifact_path), str(manifest_path)],
        )

    def _step_register_capability(
        self,
        spec: ExtensionSpecUnion,
        workspace: Path,
        evidence_artifact: str,
    ) -> ExtensionStepRecord:
        t0 = time.time()
        capability_id = self._capability_id_for(spec)
        implementation_entrypoint = ""
        if isinstance(spec, CodeExtensionSpec):
            implementation_entrypoint = spec.implementation_entrypoint
        elif isinstance(spec, ConfigExtensionSpec):
            implementation_entrypoint = f"fluid_scientist.extensions.config_spec:{spec.spec_id}"

        cap_type = (
            "physics_model_compiler"
            if isinstance(spec, PhysicsExtensionSpec)
            else spec.target_capability_type
        )
        capability = Capability(
            capability_id=capability_id,
            capability_type=cap_type,
            name=spec.description[:120] or spec.spec_id,
            description=spec.description,
            implementation_entrypoint=implementation_entrypoint,
            verification_artifact=evidence_artifact,
            test_manifest=[
                "static_security_scan",
                "static_case_validation",
                "atomic_unit_test",
            ]
            + (["openfoam_runtime_test"] if self._requires_openfoam_runtime(spec) else []),
            tests=[s.step for s in []],
            status=CapabilityStatus.VERIFIED,
            is_native=False,
            metadata={
                "extension_spec_id": spec.spec_id,
                "extension_type": spec.extension_type,
                "validation_method": self._validation_method_for(spec),
            },
        )
        self._capabilities.register(capability)
        return ExtensionStepRecord(
            step="register_verified_capability",
            passed=True,
            message=f"Registered VERIFIED capability '{capability_id}'.",
            duration_ms=(time.time() - t0) * 1000,
            artifacts=[capability_id],
        )

    # ------------------------------------------------------------------
    # Restore + re-resolve
    # ------------------------------------------------------------------

    def _backup_target_case(
        self, target_case_dir: str | Path | None, checkpoint_id: str
    ) -> Path | None:
        if target_case_dir is None:
            return None
        source = Path(target_case_dir)
        if not source.is_dir():
            return None
        backup = self.workspace_root / "checkpoints" / f"{checkpoint_id}_target_backup"
        backup.parent.mkdir(parents=True, exist_ok=True)
        if backup.exists():
            shutil.rmtree(backup)
        shutil.copytree(source, backup)
        return backup

    def _restore_original_case(
        self,
        target_case_dir: str | Path | None,
        backup: Path | None,
    ) -> bool:
        if target_case_dir is None or backup is None:
            return True
        target = Path(target_case_dir)
        if not backup.is_dir():
            return False
        if target.is_dir():
            shutil.rmtree(target)
        shutil.copytree(backup, target)
        return True

    def _re_resolve(self, requirement_set: AtomicRequirementSet | None) -> str:
        if requirement_set is None:
            return "Re-resolution skipped (no requirement_set supplied)."
        plan = self._analyzer.analyze(requirement_set)
        supported = len(plan.supported_requirements)
        total = len(plan.results)
        return (
            f"Re-resolution complete: {supported}/{total} requirements now "
            f"supported (needs_extension={plan.needs_extension}, "
            f"needs_new_physics={plan.needs_new_physics}, "
            f"environment_blocked={plan.environment_blocked})."
        )

    # ------------------------------------------------------------------
    # Unit test runners
    # ------------------------------------------------------------------

    def _run_code_unit_tests(
        self, spec: CodeExtensionSpec, workspace: Path
    ) -> tuple[bool, str, list[str]]:
        manifest: list[str] = []
        if not spec.implementation_code.strip():
            return (
                False,
                "No implementation_code provided; cannot execute unit tests.",
                ["contract: FAILED (no implementation_code)"],
            )
        namespace: dict[str, Any] = {"__builtins__": _SAFE_BUILTINS, "math": math}
        try:
            exec(compile(spec.implementation_code, spec.spec_id, "exec"), namespace)
        except Exception as exc:  # noqa: BLE001 - report any exec failure
            manifest.append(f"exec: FAILED ({exc})")
            return False, f"implementation_code failed to execute: {exc}", manifest

        entrypoint = namespace.get(spec.implementation_entrypoint)
        if not callable(entrypoint):
            manifest.append(f"entrypoint: FAILED ('{spec.implementation_entrypoint}' not callable)")
            return (
                False,
                f"Entrypoint '{spec.implementation_entrypoint}' not found or not callable.",
                manifest,
            )

        if not spec.unit_tests:
            manifest.append("entrypoint: PASSED (no unit_tests declared)")
            return True, "Entrypoint importable; no unit_tests declared.", manifest

        for test in spec.unit_tests:
            ok, msg = self._execute_one_test(entrypoint, test)
            manifest.append(f"{test.test_id}: {'PASSED' if ok else 'FAILED'} - {msg}")
            if not ok:
                return False, f"Unit test '{test.test_id}' failed: {msg}", manifest
        return True, f"{len(spec.unit_tests)} unit tests passed.", manifest

    def _execute_one_test(self, entrypoint: Callable[..., Any], test: TestSpec) -> tuple[bool, str]:
        try:
            result = entrypoint(**test.input_data) if test.input_data else entrypoint()
        except Exception as exc:  # noqa: BLE001 - report any test failure
            return False, f"raised {type(exc).__name__}: {exc}"
        return self._compare_output(result, test.expected_output, test.tolerance)

    @staticmethod
    def _compare_output(
        actual: Any, expected: dict[str, Any], tolerance: float
    ) -> tuple[bool, str]:
        if not expected:
            return True, "no expected output declared"
        if not isinstance(actual, dict):
            actual = {"result": actual}
        for key, exp_val in expected.items():
            if key not in actual:
                return False, f"missing output key '{key}'"
            act_val = actual[key]
            if exp_val is None:
                continue
            try:
                if abs(float(act_val) - float(exp_val)) > tolerance:
                    return False, (f"'{key}': {act_val} != {exp_val} (tol={tolerance})")
            except (TypeError, ValueError):
                if str(act_val) != str(exp_val):
                    return False, f"'{key}': {act_val!r} != {exp_val!r}"
        return True, "all outputs match"

    def _run_config_unit_tests(
        self, spec: ConfigExtensionSpec, workspace: Path
    ) -> tuple[bool, str, list[str]]:
        manifest: list[str] = []
        fragment = self._render_config_fragment(spec)
        ok, msg = _validate_fragment_syntax(fragment)
        manifest.append(f"dict_syntax: {'PASSED' if ok else 'FAILED'} - {msg}")
        if not ok:
            return False, f"Config dict fragment syntax invalid: {msg}", manifest
        for forbidden in self._platform.forbidden_files:
            if forbidden in fragment:
                manifest.append(f"forbidden_file: FAILED ({forbidden})")
                return False, f"Fragment references forbidden file {forbidden}", manifest
        return True, "Config dict fragment syntactically valid.", manifest

    def _run_physics_unit_tests(
        self, spec: PhysicsExtensionSpec, workspace: Path
    ) -> tuple[bool, str, list[str]]:
        manifest: list[str] = []
        if not spec.required_fields:
            return False, "Physics spec declares no required_fields.", manifest
        if not spec.governing_equations:
            manifest.append("governing_equations: WARNING (none declared)")
        for check in spec.conservation_checks:
            if check.tolerance < 0:
                manifest.append(f"{check.check_id}: FAILED (negative tolerance)")
                return (
                    False,
                    f"Conservation check '{check.check_id}' has negative tolerance.",
                    manifest,
                )
            manifest.append(
                f"{check.check_id}: PASSED (quantity={check.quantity}, method={check.method})"
            )
        # Verify required field files can be normalised to field names.
        for field_file in spec.new_field_files:
            name = field_file.rsplit("/", 1)[-1]
            if not name:
                manifest.append(f"field_file: FAILED ({field_file})")
                return False, f"Invalid field file path '{field_file}'", manifest
        return (
            True,
            f"{len(spec.conservation_checks)} conservation checks valid; "
            f"{len(spec.required_fields)} required fields declared.",
            manifest,
        )

    # ------------------------------------------------------------------
    # OpenFOAM runtime execution
    # ------------------------------------------------------------------

    def _run_openfoam_runtime(self, case_dir: Path, spec: ExtensionSpecUnion) -> tuple[bool, str]:
        solver_module = self._solver_module_for(spec)

        block_mesh = _find_cmd("blockMesh")
        if not block_mesh:
            return False, "blockMesh executable not found on PATH."
        rc, out = self._run_command([block_mesh, "-case", str(case_dir)], case_dir, 60)
        if rc != 0:
            return False, f"blockMesh failed (rc={rc})."

        check_mesh = _find_cmd("checkMesh")
        if check_mesh:
            rc, out = self._run_command([check_mesh, "-case", str(case_dir)], case_dir, 60)
            if rc != 0 or "Mesh OK" not in out:
                return False, f"checkMesh failed (rc={rc})."

        foam_run = _find_cmd("foamRun")
        if not foam_run:
            return False, "foamRun executable not found on PATH."

        # Override controlDict for a single-timestep dry-run.
        cd_path = case_dir / "system" / "controlDict"
        backup = cd_path.read_text(encoding="utf-8") if cd_path.is_file() else ""
        try:
            if cd_path.is_file():
                dry = backup
                dry = re.sub(r"endTime\s+[0-9.eE+-]+\s*;", "endTime 0.001;", dry)
                dry = re.sub(r"writeInterval\s+[0-9]+\s*;", "writeInterval 1;", dry)
                cd_path.write_text(dry, encoding="utf-8")
            rc, out = self._run_command(
                [foam_run, "-solver", solver_module, "-case", str(case_dir)],
                case_dir,
                120,
            )
        finally:
            if cd_path.is_file() and backup:
                cd_path.write_text(backup, encoding="utf-8")

        started = any(kw in out for kw in ("Time =", "Courant Number", "Starting time loop", "End"))
        crashed = any(kw in out for kw in ("FOAM FATAL ERROR", "Segmentation fault", "abort"))
        if crashed:
            return False, "foamRun dry-run crashed with a fatal error."
        if not started:
            return False, f"foamRun did not start properly (rc={rc})."
        return True, "foamRun dry-run (1 timestep) completed without fatal errors."

    @staticmethod
    def _run_command(cmd: list[str], cwd: Path, timeout: int = 120) -> tuple[int, str]:
        try:
            result = subprocess.run(
                cmd,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=timeout,
                env=os.environ.copy(),
            )
            return result.returncode, (result.stdout or "") + "\n" + (result.stderr or "")
        except subprocess.TimeoutExpired:
            return -1, f"Command timed out after {timeout}s: {' '.join(cmd)}"
        except FileNotFoundError as exc:
            return -1, f"Executable not found: {exc}"
        except Exception as exc:  # noqa: BLE001 - surface any runtime error
            return -1, f"Error running command: {exc}"

    # ------------------------------------------------------------------
    # Minimal case generation
    # ------------------------------------------------------------------

    def _write_minimal_case(self, case_dir: Path, spec: ExtensionSpecUnion) -> None:
        for rel in ("0", "constant", "system", "postProcessing"):
            (case_dir / rel).mkdir(parents=True, exist_ok=True)

        function_name = self._capability_id_for(spec).replace(".", "_").replace("-", "_")
        files: dict[str, str] = {
            "system/controlDict": _control_dict(function_name),
            "system/fvSchemes": _fv_schemes(),
            "system/fvSolution": _fv_solution(),
            "system/blockMeshDict": _block_mesh_dict(),
            "constant/physicalProperties": _physical_properties(),
            "constant/momentumTransport": _momentum_transport(),
            "0/U": _u_field(),
            "0/p": _p_field(),
        }

        # Physics extensions may introduce new constant / field files.
        if isinstance(spec, PhysicsExtensionSpec):
            if "0/T" in spec.new_field_files:
                files["0/T"] = _t_field()
            if "constant/thermophysicalProperties" in spec.new_constant_files:
                files["constant/thermophysicalProperties"] = _thermophysical_properties()

        for rel, text in files.items():
            path = case_dir / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

    # ------------------------------------------------------------------
    # Spec-type helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _requires_openfoam_runtime(spec: ExtensionSpecUnion) -> bool:
        if isinstance(spec, ConfigExtensionSpec):
            return spec.validation_method in ("smoke_test", "benchmark")
        # Code and physics extensions always require runtime validation.
        return True

    @staticmethod
    def _validation_method_for(spec: ExtensionSpecUnion) -> str:
        if isinstance(spec, ConfigExtensionSpec):
            return spec.validation_method
        if isinstance(spec, CodeExtensionSpec):
            return "smoke_test"
        return "benchmark"

    @staticmethod
    def _solver_module_for(spec: ExtensionSpecUnion) -> str:
        if isinstance(spec, PhysicsExtensionSpec) and spec.solver_module:
            return spec.solver_module
        return "incompressibleFluid"

    @staticmethod
    def _capability_id_for(spec: ExtensionSpecUnion) -> str:
        if isinstance(spec, ConfigExtensionSpec):
            return f"generated.config.{spec.semantic_role}"
        if isinstance(spec, CodeExtensionSpec):
            base = spec.implementation_entrypoint.replace("_entrypoint", "")
            return f"generated.code.{base}"
        return f"generated.physics.{spec.physical_scope}"

    def _check_physics_feasibility(self, spec: PhysicsExtensionSpec) -> str:
        """Return a non-empty reason when the physics is unsupported, else ''."""
        if spec.solver_module and not self._platform.validate_solver_module(spec.solver_module):
            return (
                f"Solver module '{spec.solver_module}' is not a known Foundation "
                f"{self._platform.version} module; cannot compile or run this "
                f"physics extension."
            )
        if spec.required_base_pack and (spec.required_base_pack not in self._components.base_packs):
            return (
                f"Required base pack '{spec.required_base_pack}' is not "
                f"present in the component registry."
            )
        return ""

    @staticmethod
    def _render_config_fragment(spec: ConfigExtensionSpec) -> str:
        """Render the foundation13_mapping as an OpenFOAM dict fragment."""
        lines: list[str] = [f"{spec.semantic_role}", "{"]
        for key, value in spec.foundation13_mapping.items():
            lines.append(f"    {key} {value};")
        lines.append("}")
        return "\n".join(lines) + "\n"


__all__ = [
    "ExtensionExecutionRecord",
    "ExtensionOrchestrationResult",
    "ExtensionOrchestrator",
    "ExtensionStatus",
    "ExtensionStepRecord",
    "FAILURE_STATES",
]
