from __future__ import annotations

import pytest
from pydantic import ValidationError

from fluid_scientist.case_ir import BoundaryIntent, Entity, PhysicsIntent, RequestedCaseIR
from fluid_scientist.openfoam_compiler import (
    CompileDiagnosticAdvice,
    CompilePlan,
    DeterministicCompilePlanCompiler,
)


def make_case_ir(*, heat_transfer: bool = False) -> RequestedCaseIR:
    return RequestedCaseIR(
        study_id="study_compile",
        case_id="case_compile",
        case_family="cylinder_flow",
        dimensionality="2D",
        physics=PhysicsIntent(heat_transfer=heat_transfer),
        entities=[Entity(id="cylinder_1", kind="cylinder")],
        boundary_intents=[
            BoundaryIntent(id="bc_inlet", target_patch="inlet", semantic_role="fixedValue"),
            BoundaryIntent(id="bc_outlet", target_patch="outlet", semantic_role="zeroGradient"),
        ],
    )


def make_plan(*, solver: str = "incompressibleFluid") -> CompilePlan:
    return CompilePlan(
        plan_id="compile_plan_1",
        case_ir_version=1,
        solver_module=solver,
        mesh_backend="snappyHexMesh",
        field_names=["U", "p"],
        function_objects=["forceCoeffs"],
        source_paths={
            "0/U": ["/boundary_intents/bc_inlet"],
            "system/controlDict": ["/physics/time_mode", "/observables"],
        },
    )


def test_model_compile_plan_cannot_contain_arbitrary_files_or_commands() -> None:
    payload = make_plan().model_dump(mode="json")
    payload["files"] = {"system/controlDict": "arbitrary model text"}
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        CompilePlan.model_validate(payload)

    payload = make_plan().model_dump(mode="json")
    payload["command"] = "rm -rf /"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        CompilePlan.model_validate(payload)


def test_deterministic_ast_renderer_generates_foundation13_files_and_source_map() -> None:
    compiler = DeterministicCompilePlanCompiler()
    ast = compiler.build_ast(make_plan(), make_case_ir())
    first = compiler.render(ast)
    second = compiler.render(ast)

    assert first.sha256 == second.sha256
    assert first.files == second.files
    assert "constant/physicalProperties" in first.files
    assert "constant/momentumTransport" in first.files
    assert "constant/transportProperties" not in first.files
    assert "system/snappyHexMeshDict" in first.files
    assert "fluidScientist/compilePlan.json" in first.files
    assert "fluidScientist/sourceMap.json" in first.files
    assert first.source_map["0/U"] == ["/boundary_intents/bc_inlet"]
    assert set(first.files).issubset(compiler._ALLOWED_PATHS)


def test_compile_plan_version_mismatch_is_rejected() -> None:
    plan = make_plan().model_copy(update={"case_ir_version": 2})
    with pytest.raises(ValueError, match="VERSION_MISMATCH"):
        DeterministicCompilePlanCompiler().build_ast(plan, make_case_ir())


def test_heat_transfer_requires_buoyant_solver() -> None:
    with pytest.raises(ValueError, match="BUOYANT_SOLVER"):
        DeterministicCompilePlanCompiler().build_ast(
            make_plan(solver="incompressibleFluid"), make_case_ir(heat_transfer=True)
        )
    ast = DeterministicCompilePlanCompiler().build_ast(
        make_plan(solver="buoyantFluid"), make_case_ir(heat_transfer=True)
    )
    assert ast.plan.solver_module == "buoyantFluid"


def test_model_diagnostic_advice_cannot_execute_shell_or_write_files() -> None:
    advice = CompileDiagnosticAdvice(
        diagnosis="checkMesh reports high non-orthogonality",
        evidence_lines=["max non-orthogonality = 82"],
        suggested_actions=["adjust_mesh_resolution"],
        affected_paths=["/mesh_intent/refinement_zones"],
        confidence=0.9,
    )
    assert advice.suggested_actions == ["adjust_mesh_resolution"]

    with pytest.raises(ValidationError):
        CompileDiagnosticAdvice.model_validate({
            **advice.model_dump(),
            "suggested_actions": ["execute_shell_command"],
        })
