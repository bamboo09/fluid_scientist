"""Config-extension execution for low-risk OpenFOAM dictionary additions."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from fluid_scientist.capabilities.orchestrator import ExtensionRunRecord
from fluid_scientist.case_generation.validator import (
    CompileReadinessReport,
    CompileReadinessValidator,
)


class ConfigExtensionExecution(BaseModel):
    """Concrete result from executing a config extension spec."""

    record: ExtensionRunRecord
    case_dir: str
    generated_files: list[str] = Field(default_factory=list)
    validation_report: dict[str, Any] = Field(default_factory=dict)
    verification_artifact: str = ""


class ConfigExtensionExecutor:
    """Generate and validate OpenFOAM config-only capability extensions."""

    def __init__(self, validator: CompileReadinessValidator | None = None) -> None:
        self._validator = validator or CompileReadinessValidator()

    def execute(
        self,
        record: ExtensionRunRecord,
        *,
        run_openfoam: bool = True,
    ) -> ConfigExtensionExecution:
        if record.spec.extension_kind != "CONFIG_EXTENSION":
            failed = record.model_copy(update={
                "status": "FAILED",
                "error": "ConfigExtensionExecutor received a non-config spec.",
            })
            return ConfigExtensionExecution(record=failed, case_dir="")

        workspace = Path(record.workspace)
        case_dir = workspace / "minimal_case"
        generated = self._write_minimal_function_object_case(case_dir, record)
        generated_record = record.model_copy(update={
            "status": "GENERATED",
            "logs": [*record.logs, "Generated minimal functionObject case."],
        })

        case_dict = _case_dict()
        report = self._validator.validate(
            case_dir,
            case_dict=case_dict,
            design={"resolved_values": {"Re": 100.0, "nu": 1e-6, "U_ref": 1.0}},
            run_openfoam=run_openfoam,
        )
        static_errors = [
            check for check in report.checks
            if (
                not check.passed
                and check.severity == "error"
                and check.check_name != "openfoam_runtime"
            )
        ]
        if static_errors:
            failed = generated_record.model_copy(update={
                "status": "FAILED",
                "error": "; ".join(
                    f"{check.check_name}: {check.message}"
                    for check in static_errors
                ),
            })
            return ConfigExtensionExecution(
                record=failed,
                case_dir=str(case_dir),
                generated_files=generated,
                validation_report=report.model_dump(),
            )

        if not run_openfoam:
            static_record = generated_record.model_copy(update={
                "status": "STATIC_VALIDATED",
                "logs": [
                    *generated_record.logs,
                    "Static validation passed; OpenFOAM runtime not requested.",
                ],
            })
            return ConfigExtensionExecution(
                record=static_record,
                case_dir=str(case_dir),
                generated_files=generated,
                validation_report=report.model_dump(),
            )

        if not report.compile_ready:
            failed = generated_record.model_copy(update={
                "status": "FAILED",
                "error": "; ".join(report.errors) or "OpenFOAM validation failed.",
            })
            return ConfigExtensionExecution(
                record=failed,
                case_dir=str(case_dir),
                generated_files=generated,
                validation_report=report.model_dump(),
            )

        artifact = _write_verification_artifact(workspace, report)
        verified_record = generated_record.model_copy(update={
            "status": "OPENFOAM_TESTED",
            "logs": [*generated_record.logs, "OpenFOAM minimal case passed."],
            "spec": generated_record.spec.model_copy(update={
                "generated_artifacts": [*generated_record.spec.generated_artifacts, artifact],
            }),
        })
        return ConfigExtensionExecution(
            record=verified_record,
            case_dir=str(case_dir),
            generated_files=generated,
            validation_report=report.model_dump(),
            verification_artifact=artifact,
        )

    def _write_minimal_function_object_case(
        self,
        case_dir: Path,
        record: ExtensionRunRecord,
    ) -> list[str]:
        for rel in ("0", "constant", "system", "postProcessing"):
            (case_dir / rel).mkdir(parents=True, exist_ok=True)
        files = {
            "system/controlDict": _control_dict(record),
            "system/fvSchemes": _fv_schemes(),
            "system/fvSolution": _fv_solution(),
            "system/blockMeshDict": _block_mesh_dict(),
            "constant/transportProperties": _transport_properties(),
            "0/U": _u_field(),
            "0/p": _p_field(),
        }
        for rel, text in files.items():
            path = case_dir / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        return sorted(files)


def _control_dict(record: ExtensionRunRecord) -> str:
    function_name = record.spec.capability_id.replace(".", "_").replace("-", "_")
    return f"""FoamFile
{{
    version 2.0;
    format ascii;
    class dictionary;
    object controlDict;
}}
application icoFoam;
startFrom startTime;
startTime 0;
stopAt endTime;
endTime 0.001;
deltaT 0.001;
writeControl timeStep;
writeInterval 1;
purgeWrite 0;
functions
{{
    {function_name}
    {{
        type residuals;
        libs ("libutilityFunctionObjects.so");
        fields (U p);
    }}
}}
"""


def _fv_schemes() -> str:
    return """FoamFile
{
    version 2.0;
    format ascii;
    class dictionary;
    object fvSchemes;
}
ddtSchemes { default Euler; }
gradSchemes { default Gauss linear; }
divSchemes { default none; div(phi,U) Gauss linear; }
laplacianSchemes { default Gauss linear corrected; }
interpolationSchemes { default linear; }
snGradSchemes { default corrected; }
"""


def _fv_solution() -> str:
    return """FoamFile
{
    version 2.0;
    format ascii;
    class dictionary;
    object fvSolution;
}
solvers
{
    p { solver PCG; preconditioner DIC; tolerance 1e-06; relTol 0; }
    U { solver smoothSolver; smoother symGaussSeidel; tolerance 1e-05; relTol 0; }
}
PISO { nCorrectors 2; nNonOrthogonalCorrectors 0; }
"""


def _block_mesh_dict() -> str:
    return """FoamFile
{
    version 2.0;
    format ascii;
    class dictionary;
    object blockMeshDict;
}
convertToMeters 1;
vertices
(
    (0 0 0) (1 0 0) (1 1 0) (0 1 0)
    (0 0 0.1) (1 0 0.1) (1 1 0.1) (0 1 0.1)
);
blocks ( hex (0 1 2 3 4 5 6 7) (4 4 1) simpleGrading (1 1 1) );
edges ();
boundary
(
    inlet
    {
        type patch;
        faces ((0 4 7 3));
    }
    outlet
    {
        type patch;
        faces ((1 2 6 5));
    }
    walls
    {
        type wall;
        faces ((0 1 5 4) (3 7 6 2));
    }
    frontAndBack
    {
        type empty;
        faces ((0 3 2 1) (4 5 6 7));
    }
);
mergePatchPairs ();
"""


def _transport_properties() -> str:
    return """FoamFile
{
    version 2.0;
    format ascii;
    class dictionary;
    object transportProperties;
}
nu [0 2 -1 0 0 0 0] 1e-06;
"""


def _u_field() -> str:
    return """FoamFile
{
    version 2.0;
    format ascii;
    class volVectorField;
    object U;
}
dimensions [0 1 -1 0 0 0 0];
internalField uniform (1 0 0);
boundaryField
{
    inlet { type fixedValue; value uniform (1 0 0); }
    outlet { type zeroGradient; }
    walls { type noSlip; }
    frontAndBack { type empty; }
}
"""


def _p_field() -> str:
    return """FoamFile
{
    version 2.0;
    format ascii;
    class volScalarField;
    object p;
}
dimensions [0 2 -2 0 0 0 0];
internalField uniform 0;
boundaryField
{
    inlet { type zeroGradient; }
    outlet { type fixedValue; value uniform 0; }
    walls { type zeroGradient; }
    frontAndBack { type empty; }
}
"""


def _case_dict() -> dict[str, Any]:
    return {
        "system": {
            "controlDict": {},
            "fvSchemes": {},
            "fvSolution": {},
        },
        "constant": {"transportProperties": {}},
        "0": {"U": {}, "p": {}},
    }


def _write_verification_artifact(
    workspace: Path,
    report: CompileReadinessReport,
) -> str:
    payload = report.model_dump_json(indent=2)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    artifact_path = workspace / "verification_artifact.json"
    artifact_path.write_text(payload, encoding="utf-8")
    return f"sha256:{digest}"


__all__ = [
    "ConfigExtensionExecution",
    "ConfigExtensionExecutor",
]
