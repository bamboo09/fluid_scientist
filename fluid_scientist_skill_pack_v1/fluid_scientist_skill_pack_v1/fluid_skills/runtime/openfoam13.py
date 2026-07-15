from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from .models import SkillIssue, SkillResult

CommandRunner = Callable[[list[str]], tuple[int, str, str]]

REQUIRED_INCOMPRESSIBLE_FILES = [
    "system/controlDict",
    "system/fvSchemes",
    "system/fvSolution",
    "constant/physicalProperties",
    "constant/momentumTransport",
    "0/U",
    "0/p",
]

FORBIDDEN_TOKENS = [
    "codeStream",
    "systemCall",
    "codedFixedValue",
    "codedMixed",
]

def platform_discovery(runner: CommandRunner) -> SkillResult:
    commands = [
        ["foamVersion"],
        ["foamRun", "-help"],
        ["foamToC", "-solvers"],
        ["foamToC", "-vectorBCs"],
        ["foamToC", "-scalarBCs"],
        ["foamToC", "-functionObjects"],
        ["foamPostProcess", "-list"],
    ]
    evidence = []
    issues: list[SkillIssue] = []
    outputs: dict[str, str] = {}

    for cmd in commands:
        code, stdout, stderr = runner(cmd)
        key = " ".join(cmd)
        evidence.append({"command": cmd, "exit_code": code, "stderr": stderr[-2000:]})
        if code != 0:
            issues.append(SkillIssue(
                code="OPENFOAM_DISCOVERY_COMMAND_FAILED",
                message=f"命令失败：{key}",
                blocking=True,
                details={"exit_code": code, "stderr": stderr[-2000:]},
            ))
        outputs[key] = stdout

    version_text = outputs.get("foamVersion", "")
    foundation13 = bool(re.search(r"(OpenFOAM[- ]?13|\b13\b)", version_text, re.I))
    if not foundation13:
        issues.append(SkillIssue(
            code="OPENFOAM_VERSION_MISMATCH",
            message="目标环境不是已确认的 OpenFOAM Foundation 13。",
            blocking=True,
            details={"foamVersion": version_text.strip()},
        ))

    return SkillResult(
        skill_id="openfoam13.platform.discovery",
        status="FAILED" if issues else "SUCCESS",
        data={
            "distribution": "OpenFOAMFoundation" if foundation13 else "UNKNOWN",
            "version": "13" if foundation13 else None,
            "application": "foamRun",
            "solver_module": "incompressibleFluid",
            "raw_outputs": outputs,
        },
        issues=issues,
        evidence=evidence,
    )

def static_validate_case(case_dir: str | Path) -> SkillResult:
    case_path = Path(case_dir)
    issues: list[SkillIssue] = []
    evidence: list[dict[str, Any]] = []

    for relative in REQUIRED_INCOMPRESSIBLE_FILES:
        path = case_path / relative
        exists = path.exists()
        evidence.append({"file": relative, "exists": exists})
        if not exists:
            issues.append(SkillIssue(
                code="REQUIRED_OPENFOAM_FILE_MISSING",
                message=f"缺少文件：{relative}",
                blocking=True,
                path=relative,
            ))

    legacy = case_path / "constant" / "transportProperties"
    if legacy.exists():
        issues.append(SkillIssue(
            code="LEGACY_TRANSPORT_PROPERTIES_FORBIDDEN",
            message="当前 Foundation 13 不可压缩模板不应生成 constant/transportProperties。",
            blocking=True,
            path="constant/transportProperties",
        ))

    for path in case_path.rglob("*"):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for token in FORBIDDEN_TOKENS:
            if token in text:
                issues.append(SkillIssue(
                    code="FORBIDDEN_OPENFOAM_TOKEN",
                    message=f"发现禁止关键字：{token}",
                    blocking=True,
                    path=str(path.relative_to(case_path)),
                ))

    control = case_path / "system" / "controlDict"
    if control.exists():
        text = control.read_text(encoding="utf-8", errors="ignore")
        if "incompressibleFluid" not in text:
            issues.append(SkillIssue(
                code="SOLVER_MODULE_MISMATCH",
                message="controlDict 未声明 incompressibleFluid。",
                blocking=True,
                path="system/controlDict",
            ))

    return SkillResult(
        skill_id="openfoam13.case.static_validator",
        status="FAILED" if issues else "SUCCESS",
        data={"case_dir": str(case_path)},
        issues=issues,
        evidence=evidence,
    )

def smoke_test_plan(parallel: bool = False) -> SkillResult:
    commands = [
        ["foamDictionary", "system/controlDict", "-entry", "solver"],
        ["checkMesh", "-allTopology", "-allGeometry"],
        ["timeout", "120", "foamRun", "-solver", "incompressibleFluid"],
    ]
    if parallel:
        commands += [
            ["decomposePar", "-force"],
            ["mpirun", "-np", "2", "foamRun", "-solver", "incompressibleFluid", "-parallel"],
        ]
    return SkillResult(
        skill_id="validation.smoke_test",
        status="SUCCESS",
        data={
            "commands": commands,
            "failure_markers": [
                "FOAM FATAL ERROR",
                "Floating point exception",
                "nan",
                "inf",
            ],
            "success_requirements": [
                "exit_code_zero",
                "completed_iterations_or_timesteps",
                "expected_outputs_exist",
                "no_failure_markers",
            ],
        },
    )
