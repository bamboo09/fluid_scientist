"""Structured model-assisted CompilePlan and deterministic Foundation 13 renderer."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from fluid_scientist.case_ir.models import RequestedCaseIR


class CompilePlan(BaseModel):
    """Safe model output: selections only, never arbitrary file content."""

    model_config = ConfigDict(extra="forbid")
    plan_id: str
    case_ir_version: int
    target_platform: Literal["openfoam-foundation-13"] = "openfoam-foundation-13"
    application: Literal["foamRun"] = "foamRun"
    solver_module: Literal["incompressibleFluid", "fluid", "buoyantFluid", "multiphaseVoFSolver"]
    mesh_backend: Literal["blockMesh", "snappyHexMesh"]
    field_names: list[Literal["U", "p", "T", "k", "omega", "nut"]]
    function_objects: list[Literal[
        "forces", "forceCoeffs", "probes", "fieldAverage", "wallShearStress", "yPlus"
    ]] = Field(default_factory=list)
    requested_validations: list[Literal[
        "static", "blockMesh", "snappyHexMesh", "checkMesh", "smoke"
    ]] = Field(default_factory=lambda: ["static", "blockMesh", "checkMesh", "smoke"])
    rationale: list[str] = Field(default_factory=list)
    source_paths: dict[str, list[str]] = Field(default_factory=dict)


class CompileDiagnosticAdvice(BaseModel):
    """Model diagnostic advice constrained to reviewable actions."""

    model_config = ConfigDict(extra="forbid")
    diagnosis: str
    evidence_lines: list[str] = Field(default_factory=list)
    suggested_actions: list[Literal[
        "revise_compile_plan", "revise_case_ir", "adjust_mesh_resolution",
        "adjust_time_step", "request_user_clarification", "no_action",
    ]] = Field(default_factory=list)
    affected_paths: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class CompilePlanAST(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan: CompilePlan
    case_family: str
    dimensionality: Literal["2D", "3D"]
    boundary_roles: dict[str, str] = Field(default_factory=dict)
    physical_properties: dict[str, Any] = Field(default_factory=dict)


class RenderedFoundation13Case(BaseModel):
    model_config = ConfigDict(extra="forbid")
    files: dict[str, str]
    sha256: str
    source_map: dict[str, list[str]]


class DeterministicCompilePlanCompiler:
    """Validate plan against CaseIR, build AST, and render fixed paths."""

    _ALLOWED_PATHS = (
        "0/U", "0/p", "0/T", "0/k", "0/omega", "0/nut",
        "constant/physicalProperties", "constant/momentumTransport",
        "system/blockMeshDict", "system/snappyHexMeshDict", "system/controlDict",
        "system/fvSchemes", "system/fvSolution", "system/decomposeParDict",
        "fluidScientist/compilePlan.json", "fluidScientist/sourceMap.json",
    )

    def build_ast(self, plan: CompilePlan, case_ir: RequestedCaseIR) -> CompilePlanAST:
        if plan.case_ir_version != case_ir.case_ir_version:
            raise ValueError("COMPILE_PLAN_VERSION_MISMATCH")
        if case_ir.physics.heat_transfer and plan.solver_module != "buoyantFluid":
            raise ValueError("HEAT_TRANSFER_REQUIRES_BUOYANT_SOLVER")
        if case_ir.dimensionality == "3D" and not case_ir.entities:
            raise ValueError("THREE_DIMENSIONAL_CASE_REQUIRES_GEOMETRY")
        return CompilePlanAST(
            plan=plan,
            case_family=case_ir.case_family,
            dimensionality=case_ir.dimensionality,
            boundary_roles={
                boundary.target_patch: boundary.semantic_role
                for boundary in case_ir.boundary_intents
            },
            physical_properties={
                f"{material.id}.{name}": parameter.model_dump(mode="json")
                for material in case_ir.materials
                for name, parameter in material.properties.items()
            },
        )

    def render(self, ast: CompilePlanAST) -> RenderedFoundation13Case:
        plan = ast.plan
        files: dict[str, str] = {
            "system/controlDict": self._dictionary("controlDict", {
                "application": plan.application, "solver": plan.solver_module, "writeFormat": "ascii",
            }),
            "system/fvSchemes": self._dictionary("fvSchemes", {"ddtSchemes": "Euler"}),
            "system/fvSolution": self._dictionary("fvSolution", {"algorithm": "PIMPLE"}),
            "system/blockMeshDict": self._dictionary("blockMeshDict", {
                "caseFamily": ast.case_family, "dimensionality": ast.dimensionality,
            }),
            "constant/physicalProperties": self._dictionary("physicalProperties", {"viscosityModel": "constant"}),
            "constant/momentumTransport": self._dictionary("momentumTransport", {"simulationType": "laminar"}),
            "system/decomposeParDict": self._dictionary("decomposeParDict", {"numberOfSubdomains": 1}),
        }
        if plan.mesh_backend == "snappyHexMesh":
            files["system/snappyHexMeshDict"] = self._dictionary(
                "snappyHexMeshDict", {"castellatedMesh": "true", "snap": "true"}
            )
        for field_name in plan.field_names:
            files[f"0/{field_name}"] = self._field(field_name, ast.boundary_roles)
        source_map = {path: list(plan.source_paths.get(path, [])) for path in files}
        files["fluidScientist/compilePlan.json"] = json.dumps(
            plan.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, indent=2
        )
        source_map["fluidScientist/compilePlan.json"] = ["/compile_plan"]
        files["fluidScientist/sourceMap.json"] = json.dumps(
            source_map, ensure_ascii=False, sort_keys=True, indent=2
        )
        if not set(files).issubset(self._ALLOWED_PATHS):
            raise ValueError("RENDERER_PRODUCED_UNAPPROVED_PATH")
        digest = hashlib.sha256()
        for path in sorted(files):
            digest.update(path.encode())
            digest.update(b"\0")
            digest.update(files[path].encode())
            digest.update(b"\0")
        return RenderedFoundation13Case(files=files, sha256=digest.hexdigest(), source_map=source_map)

    @staticmethod
    def _dictionary(object_name: str, entries: dict[str, Any]) -> str:
        lines = [
            "FoamFile", "{", "    version 2.0;", "    format ascii;",
            "    class dictionary;", f"    object {object_name};", "}", "",
        ]
        lines.extend(f"{key} {value};" for key, value in entries.items())
        return "\n".join(lines) + "\n"

    @staticmethod
    def _field(name: str, boundaries: dict[str, str]) -> str:
        field_class = "volVectorField" if name == "U" else "volScalarField"
        internal = "uniform (0 0 0)" if name == "U" else "uniform 0"
        patches = "\n".join(
            f"    {patch} {{ type {role}; }}" for patch, role in sorted(boundaries.items())
        )
        return (
            "FoamFile\n{\n    version 2.0;\n    format ascii;\n"
            f"    class {field_class};\n    object {name};\n}}\n"
            f"internalField {internal};\nboundaryField\n{{\n{patches}\n}}\n"
        )
