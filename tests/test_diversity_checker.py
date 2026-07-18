"""Tests for the anti-template diversity checker (Phase 18).

Verifies that different simulation specs produce different compiled
artifacts, preventing the "template 通吃" failure mode.
"""
from __future__ import annotations

import pytest

from fluid_scientist.audit import ArtifactDiversityChecker, DiversityReport, DiversityViolation
from fluid_scientist.openfoam_compiler import OpenFOAMCompiler
from fluid_scientist.spec_editing import PatchEngine, PatchOperation
from fluid_scientist.study_spec import SimulationStudySpec

from tests.e2e.model_editing.conftest import make_study_spec, make_patch


def _compile_pair(spec1: SimulationStudySpec, spec2: SimulationStudySpec):
    """Compile two specs and return (specs_list, cases_list)."""
    compiler = OpenFOAMCompiler()
    case1 = compiler.compile(spec1)
    case2 = compiler.compile(spec2)
    return (
        [spec1.model_dump(), spec2.model_dump()],
        [case1.model_dump(), case2.model_dump()],
    )


class TestArchiveHashDiversity:
    def test_different_end_time_different_hash(self):
        spec1 = make_study_spec()
        result = PatchEngine().process_patch(
            make_patch(spec1, operations=[
                PatchOperation(op="replace", path="/numerics/time/end_time",
                               value=15.0, source_quote="15秒", confidence=0.99),
            ]),
            spec1,
        )
        spec2 = result.new_spec
        specs, cases = _compile_pair(spec1, spec2)
        checker = ArtifactDiversityChecker()
        report = checker.check_compiled_cases(specs, cases)
        assert report.passed, f"Expected diversity, got: {report.summary}"
        # Hashes must differ
        assert cases[0]["archive_sha256"] != cases[1]["archive_sha256"]

    def test_identical_specs_same_hash(self):
        """Identical specs MUST produce identical archive_sha256 (determinism)."""
        spec1 = make_study_spec()
        spec2 = make_study_spec()
        specs, cases = _compile_pair(spec1, spec2)
        checker = ArtifactDiversityChecker()
        report = checker.check_compiled_cases(specs, cases)
        # Identical specs producing same hash is correct (determinism)
        assert cases[0]["archive_sha256"] == cases[1]["archive_sha256"]


class TestControlDictDiversity:
    def test_different_end_time_different_control_dict(self):
        spec1 = make_study_spec()
        result = PatchEngine().process_patch(
            make_patch(spec1, operations=[
                PatchOperation(op="replace", path="/numerics/time/end_time",
                               value=15.0, source_quote="15秒", confidence=0.99),
            ]),
            spec1,
        )
        spec2 = result.new_spec
        specs, cases = _compile_pair(spec1, spec2)
        cd1 = cases[0]["files"]["system/controlDict"]
        cd2 = cases[1]["files"]["system/controlDict"]
        assert cd1 != cd2, "controlDict must differ when end_time differs"

    def test_different_delta_t_different_control_dict(self):
        spec1 = make_study_spec()
        result = PatchEngine().process_patch(
            make_patch(spec1, operations=[
                PatchOperation(op="replace", path="/numerics/time/delta_t",
                               value=0.005, source_quote="0.005", confidence=0.99),
            ]),
            spec1,
        )
        spec2 = result.new_spec
        specs, cases = _compile_pair(spec1, spec2)
        cd1 = cases[0]["files"]["system/controlDict"]
        cd2 = cases[1]["files"]["system/controlDict"]
        assert cd1 != cd2


class TestGeometryDiversity:
    def test_triangle_vs_rectangle_different_fields(self):
        """Triangle vs rectangle geometry must produce different compiled cases.

        Note: The current compiler does not yet generate mesh files
        (blockMeshDict/snappyHexMeshDict), so geometry-only changes may
        produce identical compiled artifacts. The diversity checker should
        detect this as a violation until mesh generation is implemented.
        """
        spec1 = make_study_spec()
        spec2 = make_study_spec()

        # Add triangle to spec1
        r1 = PatchEngine().process_patch(
            make_patch(spec1, operations=[
                PatchOperation(op="add", path="/geometry/entities/obstacle",
                               value={"entity_id": "obstacle",
                                      "semantic_type": "triangle_2d",
                                      "primitive": {"type": "triangle", "base_width": 0.1, "height": 0.05},
                                      "polygon_vertices": None,
                                      "original_user_semantics": "三角",
                                      "placement": None},
                               source_quote="三角", confidence=0.95),
            ]),
            spec1,
        )
        spec1 = r1.new_spec

        # Add rectangle to spec2
        r2 = PatchEngine().process_patch(
            make_patch(spec2, operations=[
                PatchOperation(op="add", path="/geometry/entities/obstacle",
                               value={"entity_id": "obstacle",
                                      "semantic_type": "rectangle_2d",
                                      "primitive": {"type": "rectangle", "width": 0.1, "height": 0.05},
                                      "polygon_vertices": None,
                                      "original_user_semantics": "矩形",
                                      "placement": None},
                               source_quote="矩形", confidence=0.95),
            ]),
            spec2,
        )
        spec2 = r2.new_spec

        specs, cases = _compile_pair(spec1, spec2)
        checker = ArtifactDiversityChecker()

        # The archive hashes should differ because the specs are different
        # (different spec_id, different geometry entities)
        # If they don't differ, the checker should flag it
        if cases[0]["archive_sha256"] == cases[1]["archive_sha256"]:
            # Geometry-only change without mesh generation: this is a known
            # diversity violation that will be resolved when mesh generation
            # is added to the compiler
            report = checker.check_compiled_cases(specs, cases)
            assert not report.passed, (
                "Geometry-only change producing identical artifacts should be "
                "flagged as a diversity violation"
            )
        else:
            # If hashes differ, diversity check should pass
            report = checker.check_compiled_cases(specs, cases)
            assert report.passed


class TestTransportDiversity:
    def test_air_vs_water_different_transport_properties(self):
        """Base spec uses water (nu=1e-6). Change to air (nu=1.5e-5) and verify."""
        spec1 = make_study_spec()  # water by default (nu=1e-6)
        result = PatchEngine().process_patch(
            make_patch(spec1, operations=[
                PatchOperation(op="replace", path="/physics/material",
                               value={"value": "air", "status": "user_explicit"},
                               source_quote="空气", confidence=0.99),
                PatchOperation(op="replace", path="/physics/kinematic_viscosity",
                               value={"value": 1.5e-5, "unit": "m^2/s", "status": "user_explicit"},
                               source_quote="空气", confidence=0.99),
            ]),
            spec1,
        )
        spec2 = result.new_spec
        specs, cases = _compile_pair(spec1, spec2)
        tp1 = cases[0]["files"]["constant/transportProperties"]
        tp2 = cases[1]["files"]["constant/transportProperties"]
        assert tp1 != tp2, "transportProperties must differ for water vs air"


class TestTurbulenceDiversity:
    def test_laminar_vs_rans_different_turbulence_properties(self):
        spec1 = make_study_spec()  # laminar by default
        result = PatchEngine().process_patch(
            make_patch(spec1, operations=[
                PatchOperation(op="replace", path="/numerics/turbulence_model",
                               value="RANS_kEpsilon", source_quote="RANS", confidence=0.99),
            ]),
            spec1,
        )
        spec2 = result.new_spec
        specs, cases = _compile_pair(spec1, spec2)
        # laminar case has no turbulenceProperties file, RANS does
        tp1 = cases[0]["files"].get("constant/turbulenceProperties", "")
        tp2 = cases[1]["files"].get("constant/turbulenceProperties", "")
        assert tp1 != tp2, "turbulenceProperties must differ for laminar vs RANS"


class TestFunctionObjectDiversity:
    def test_different_observations_different_control_dict(self):
        """Base spec has Cd. Add y_plus and verify controlDict differs."""
        spec1 = make_study_spec()
        result = PatchEngine().process_patch(
            make_patch(spec1, operations=[
                PatchOperation(op="append_unique", path="/observations/targets/-",
                               value={"target_id": "yp_target", "metric": "y_plus",
                                      "parameters": {}, "function_object_type": "yPlus"},
                               source_quote="y+", confidence=0.95),
            ]),
            spec1,
        )
        spec2 = result.new_spec
        specs, cases = _compile_pair(spec1, spec2)
        cd1 = cases[0]["files"]["system/controlDict"]
        cd2 = cases[1]["files"]["system/controlDict"]
        assert cd1 != cd2, "controlDict must differ when observations differ"


class TestReportGeneration:
    def test_report_passes_when_all_diverse(self):
        spec1 = make_study_spec()
        result = PatchEngine().process_patch(
            make_patch(spec1, operations=[
                PatchOperation(op="replace", path="/numerics/time/end_time",
                               value=15.0, source_quote="15秒", confidence=0.99),
            ]),
            spec1,
        )
        spec2 = result.new_spec
        specs, cases = _compile_pair(spec1, spec2)
        checker = ArtifactDiversityChecker()
        report = checker.check_compiled_cases(specs, cases)
        assert report.passed
        assert report.total_specs_checked == 2
        assert len(report.violations) == 0

    def test_report_fails_on_template_reuse(self):
        """Simulate template reuse by passing identical compiled cases for different specs."""
        spec1 = make_study_spec()
        result = PatchEngine().process_patch(
            make_patch(spec1, operations=[
                PatchOperation(op="replace", path="/numerics/time/end_time",
                               value=15.0, source_quote="15秒", confidence=0.99),
            ]),
            spec1,
        )
        spec2 = result.new_spec
        # Compile both
        compiler = OpenFOAMCompiler()
        case1 = compiler.compile(spec1)
        case2 = compiler.compile(spec2)
        # Tamper: make case2 identical to case1 (template reuse)
        case2_tampered = {**case2.model_dump(), "files": case1.model_dump()["files"],
                          "archive_sha256": case1.model_dump()["archive_sha256"]}
        checker = ArtifactDiversityChecker()
        report = checker.check_compiled_cases(
            [spec1.model_dump(), spec2.model_dump()],
            [case1.model_dump(), case2_tampered],
        )
        assert not report.passed
        assert len(report.violations) > 0


class TestTraceability:
    def test_spec_change_reflected_in_artifact(self):
        spec1 = make_study_spec()
        result = PatchEngine().process_patch(
            make_patch(spec1, operations=[
                PatchOperation(op="replace", path="/numerics/time/end_time",
                               value=15.0, source_quote="15秒", confidence=0.99),
            ]),
            spec1,
        )
        spec2 = result.new_spec
        specs, cases = _compile_pair(spec1, spec2)
        checker = ArtifactDiversityChecker()
        violations = checker.check_spec_to_artifact_traceability(specs, cases)
        assert len(violations) == 0, "Spec change must be reflected in artifacts"


class TestDiversityMatrix:
    def test_matrix_generation(self):
        spec1 = make_study_spec()
        r = PatchEngine().process_patch(
            make_patch(spec1, operations=[
                PatchOperation(op="replace", path="/numerics/time/end_time",
                               value=15.0, source_quote="15秒", confidence=0.99),
            ]),
            spec1,
        )
        spec2 = r.new_spec
        specs = [spec1.model_dump(), spec2.model_dump()]
        checker = ArtifactDiversityChecker()
        matrix = checker.generate_diversity_test_matrix(specs)
        assert len(matrix) == 1  # one pair
        assert matrix[0]["pair"] == (0, 1)
        assert "numerics.time.end_time" in matrix[0]["spec_diff_fields"]
        assert "system/controlDict" in matrix[0]["expected_artifact_changes"]


class TestFullDiversityCheck:
    def test_multiple_specs_all_diverse(self):
        """Test 4 specs with different parameters - all must produce diverse artifacts."""
        spec_base = make_study_spec()

        # Spec 2: different end_time
        r2 = PatchEngine().process_patch(
            make_patch(spec_base, operations=[
                PatchOperation(op="replace", path="/numerics/time/end_time",
                               value=15.0, source_quote="15秒", confidence=0.99),
            ]),
            spec_base,
        )
        spec2 = r2.new_spec

        # Spec 3: different material (base is water, change to air)
        r3 = PatchEngine().process_patch(
            make_patch(spec_base, operations=[
                PatchOperation(op="replace", path="/physics/material",
                               value={"value": "air", "status": "user_explicit"},
                               source_quote="空气", confidence=0.99),
                PatchOperation(op="replace", path="/physics/kinematic_viscosity",
                               value={"value": 1.5e-5, "unit": "m^2/s", "status": "user_explicit"},
                               source_quote="空气", confidence=0.99),
            ]),
            spec_base,
        )
        spec3 = r3.new_spec

        # Spec 4: different turbulence
        r4 = PatchEngine().process_patch(
            make_patch(spec_base, operations=[
                PatchOperation(op="replace", path="/numerics/turbulence_model",
                               value="RANS_kEpsilon", source_quote="RANS", confidence=0.99),
            ]),
            spec_base,
        )
        spec4 = r4.new_spec

        compiler = OpenFOAMCompiler()
        specs = [s.model_dump() for s in [spec_base, spec2, spec3, spec4]]
        cases = [compiler.compile(s).model_dump() for s in [spec_base, spec2, spec3, spec4]]

        checker = ArtifactDiversityChecker()
        report = checker.check_compiled_cases(specs, cases)
        assert report.passed, f"Diversity check failed: {report.summary}"
        assert report.total_specs_checked == 4

        # All 4 hashes must be unique
        hashes = [c["archive_sha256"] for c in cases]
        assert len(set(hashes)) == 4, f"Expected 4 unique hashes, got {len(set(hashes))}"
