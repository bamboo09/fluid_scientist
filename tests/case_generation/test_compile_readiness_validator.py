"""Unit tests for the CompileReadinessValidator (static validation chain)."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from fluid_scientist.case_generation.validator import (
    CompileReadinessValidator,
    _validate_foam_dictionary_syntax,
)


# ---------------------------------------------------------------------------
# Dictionary syntax parser unit tests
# ---------------------------------------------------------------------------


class TestDictionarySyntaxValidation:
    """Tests for the low-level _validate_foam_dictionary_syntax function."""

    def test_balanced_simple_dict_passes(self):
        text = textwrap.dedent("""\
            FoamFile
            {
                version 2.0;
                format ascii;
            }
            application simpleFoam;
            startFrom startTime;
        """)
        ok, msg, errs = _validate_foam_dictionary_syntax(text)
        assert ok
        assert not errs

    def test_unbalanced_opening_brace_detected(self):
        text = textwrap.dedent("""\
            solvers
            {
                p
                {
                    solver GAMG;
                }
        """)
        ok, msg, errs = _validate_foam_dictionary_syntax(text)
        assert not ok
        assert any("unbalanced braces" in e[1] for e in errs)

    def test_unbalanced_closing_brace_detected(self):
        text = textwrap.dedent("""\
            solvers
            {
                p { solver GAMG; }
            }
            }
        """)
        ok, msg, errs = _validate_foam_dictionary_syntax(text)
        assert not ok
        assert any("unexpected closing brace" in e[1] for e in errs)

    def test_balanced_parentheses_pass(self):
        text = textwrap.dedent("""\
            vertices
            (
                (0 0 0)
                (1 0 0)
                (1 1 0)
                (0 1 0)
            );
        """)
        ok, msg, errs = _validate_foam_dictionary_syntax(text)
        assert ok

    def test_unbalanced_parentheses_detected(self):
        text = textwrap.dedent("""\
            vertices
            (
                (0 0 0)
                (1 0 0
            );
        """)
        ok, msg, errs = _validate_foam_dictionary_syntax(text)
        assert not ok
        assert any("unbalanced parentheses" in e[1] for e in errs)

    def test_block_comments_are_ignored(self):
        text = textwrap.dedent("""\
            /*
             * This is a block comment
             * with { unbalanced braces }
             * and ( parens ) inside
             */
            application simpleFoam;
        """)
        ok, msg, errs = _validate_foam_dictionary_syntax(text)
        assert ok

    def test_line_comments_are_ignored(self):
        text = textwrap.dedent("""\
            // This comment has { braces } and ( parens )
            application simpleFoam;  // inline comment with } {
        """)
        ok, msg, errs = _validate_foam_dictionary_syntax(text)
        assert ok

    def test_multiple_braces_per_line_counted_correctly(self):
        text = "solvers { p { solver GAMG; } U { solver PBiCG; } }"
        ok, msg, errs = _validate_foam_dictionary_syntax(text)
        assert ok

    def test_empty_file_passes(self):
        ok, msg, errs = _validate_foam_dictionary_syntax("")
        assert ok


# ---------------------------------------------------------------------------
# Helpers to build minimal case directories
# ---------------------------------------------------------------------------


def _write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _foam_file(class_name: str, object_name: str, body: str = "") -> str:
    return textwrap.dedent(f"""\
        FoamFile
        {{
            version 2.0;
            format ascii;
            class {class_name};
            object {object_name};
        }}
        {body}
    """)


def _minimal_case(case_dir: Path, *, with_snappy: bool = False) -> None:
    """Create a minimal valid OpenFOAM case for testing static validation."""
    # controlDict
    _write_file(
        case_dir / "system" / "controlDict",
        _foam_file(
            "dictionary", "controlDict",
            textwrap.dedent("""\
                application simpleFoam;
                startFrom startTime;
                startTime 0;
                stopAt endTime;
                endTime 100;
                deltaT 1;
                writeControl timeStep;
                writeInterval 50;
                functions
                {
                    residuals
                    {
                        type residuals;
                        functionObjectLibs ("libutilityFunctionObjects.so");
                        fields (U p);
                    }
                }
            """),
        ),
    )

    # fvSchemes
    _write_file(
        case_dir / "system" / "fvSchemes",
        _foam_file(
            "dictionary", "fvSchemes",
            textwrap.dedent("""\
                ddtSchemes { default steadyState; }
                gradSchemes { default Gauss linear; }
                divSchemes { default none; }
                laplacianSchemes { default Gauss linear corrected; }
                interpolationSchemes { default linear; }
                snGradSchemes { default corrected; }
            """),
        ),
    )

    # fvSolution
    _write_file(
        case_dir / "system" / "fvSolution",
        _foam_file(
            "dictionary", "fvSolution",
            textwrap.dedent("""\
                solvers
                {
                    p { solver GAMG; tolerance 1e-7; relTol 0.01; }
                    U { solver PBiCGStab; preconditioner DILU; tolerance 1e-6; relTol 0.1; }
                }
                SIMPLE { nNonOrthogonalCorrectors 0; }
            """),
        ),
    )

    # blockMeshDict
    _write_file(
        case_dir / "system" / "blockMeshDict",
        _foam_file(
            "dictionary", "blockMeshDict",
            textwrap.dedent("""\
                convertToMeters 1;
                vertices
                (
                    (0 0 0)
                    (1 0 0)
                    (1 1 0)
                    (0 1 0)
                    (0 0 0.1)
                    (1 0 0.1)
                    (1 1 0.1)
                    (0 1 0.1)
                );
                blocks
                (
                    hex (0 1 2 3 4 5 6 7) (10 10 1) simpleGrading (1 1 1)
                );
                edges ();
                boundary
                (
                    inlet
                    {
                        type patch;
                        faces
                        (
                            (0 3 7 4)
                        );
                    }
                    outlet
                    {
                        type patch;
                        faces
                        (
                            (1 5 6 2)
                        );
                    }
                    walls
                    {
                        type wall;
                        faces
                        (
                            (0 4 5 1)
                            (3 2 6 7)
                            (0 1 2 3)
                            (4 7 6 5)
                        );
                    }
                    front
                    {
                        type empty;
                        faces
                        (
                            (4 5 6 7)
                        );
                    }
                    back
                    {
                        type empty;
                        faces
                        (
                            (0 3 2 1)
                        );
                    }
                );
                mergePatchPairs ();
            """),
        ),
    )

    # U
    _write_file(
        case_dir / "0" / "U",
        _foam_file(
            "volVectorField", "U",
            textwrap.dedent("""\
                dimensions [0 1 -1 0 0 0 0];
                internalField uniform (1 0 0);
                boundaryField
                {
                    inlet { type fixedValue; value uniform (1 0 0); }
                    outlet { type zeroGradient; }
                    walls { type noSlip; }
                    front { type empty; }
                    back { type empty; }
                }
            """),
        ),
    )

    # p
    _write_file(
        case_dir / "0" / "p",
        _foam_file(
            "volScalarField", "p",
            textwrap.dedent("""\
                dimensions [0 2 -2 0 0 0 0];
                internalField uniform 0;
                boundaryField
                {
                    inlet { type zeroGradient; }
                    outlet { type fixedValue; value uniform 0; }
                    walls { type zeroGradient; }
                    front { type empty; }
                    back { type empty; }
                }
            """),
        ),
    )

    # transportProperties
    _write_file(
        case_dir / "constant" / "transportProperties",
        _foam_file(
            "dictionary", "transportProperties",
            textwrap.dedent("""\
                nu [0 2 -1 0 0 0 0] 1e-5;
            """),
        ),
    )

    if with_snappy:
        _write_file(
            case_dir / "system" / "snappyHexMeshDict",
            _foam_file(
                "dictionary", "snappyHexMeshDict",
                textwrap.dedent("""\
                    castellatedMesh true;
                    snap true;
                    addLayers true;
                    geometry
                    {
                        cylinder.stl
                        {
                            type triSurfaceMesh;
                            name body;
                        }
                    };
                    addLayers
                    {
                        layers
                        {
                            body
                            {
                                nSurfaceLayers 3;
                            }
                        }
                    }
                """),
            ),
        )


# ---------------------------------------------------------------------------
# CompileReadinessValidator static checks
# ---------------------------------------------------------------------------


class TestCompileReadinessValidatorStatic:
    """Integration tests for static validation on a minimal case."""

    def test_minimal_case_passes_all_static_checks(self, tmp_path: Path):
        case_dir = tmp_path / "case"
        _minimal_case(case_dir)
        v = CompileReadinessValidator()
        report = v.validate(str(case_dir), run_openfoam=False)

        # All static error checks should pass
        error_checks = [c for c in report.checks if c.severity == "error"]
        failed_errors = [c for c in error_checks if not c.passed]
        assert not failed_errors, f"Failed error checks: {[(c.check_name, c.message) for c in failed_errors]}"
        assert report.compile_ready is True

    def test_missing_file_detected(self, tmp_path: Path):
        case_dir = tmp_path / "case"
        _minimal_case(case_dir)
        # Remove U file
        (case_dir / "0" / "U").unlink()
        v = CompileReadinessValidator()
        report = v.validate(str(case_dir), run_openfoam=False)

        fc = next(c for c in report.checks if c.check_name == "file_completeness")
        assert not fc.passed

    def test_unbalanced_braces_detected_in_field_file(self, tmp_path: Path):
        case_dir = tmp_path / "case"
        _minimal_case(case_dir)
        # Corrupt U file with extra brace
        u_text = (case_dir / "0" / "U").read_text()
        (case_dir / "0" / "U").write_text(u_text + "\n}\n", encoding="utf-8")
        v = CompileReadinessValidator()
        report = v.validate(str(case_dir), run_openfoam=False)

        ds = next(c for c in report.checks if c.check_name == "dictionary_syntax")
        assert not ds.passed

    def test_patch_consistency_rejects_missing_patch(self, tmp_path: Path):
        case_dir = tmp_path / "case"
        _minimal_case(case_dir)
        # Add a bogus patch reference in U
        u_path = case_dir / "0" / "U"
        u_text = u_path.read_text()
        u_text = u_text.replace("front { type empty; }", "cylinder { type noSlip; }\n    front { type empty; }")
        u_path.write_text(u_text, encoding="utf-8")
        v = CompileReadinessValidator()
        report = v.validate(str(case_dir), run_openfoam=False)

        pc = next(c for c in report.checks if c.check_name == "patch_consistency")
        assert not pc.passed
        assert "cylinder" in pc.message

    def test_snappy_patches_are_allowed_as_dynamic(self, tmp_path: Path):
        case_dir = tmp_path / "case"
        _minimal_case(case_dir, with_snappy=True)
        # Add body patch to U (body is referenced in snappyHexMeshDict)
        u_path = case_dir / "0" / "U"
        p_path = case_dir / "0" / "p"
        for fpath in (u_path, p_path):
            text = fpath.read_text()
            text = text.replace("walls {", "body { type noSlip; }\n        walls {")
            # p file uses zeroGradient for walls; add body similarly
            text = text.replace("walls { type zeroGradient; }", "body { type zeroGradient; }\n        walls { type zeroGradient; }")
            fpath.write_text(text, encoding="utf-8")
        v = CompileReadinessValidator()
        report = v.validate(str(case_dir), run_openfoam=False)

        pc = next(c for c in report.checks if c.check_name == "patch_consistency")
        assert pc.passed, f"patch_consistency failed: {pc.message}"

    def test_function_objects_detected(self, tmp_path: Path):
        case_dir = tmp_path / "case"
        _minimal_case(case_dir)
        v = CompileReadinessValidator()
        report = v.validate(str(case_dir), run_openfoam=False)

        