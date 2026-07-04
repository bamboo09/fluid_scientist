from __future__ import annotations

import gzip
import io
import tarfile

import pytest
from pydantic import ValidationError

from fluid_scientist.case_generation.models import GeneratedCaseDraft
from fluid_scientist.case_generation.rendering import render_defaults, render_generated_case
from fluid_scientist.case_generation.validation import (
    GeneratedCaseRejected,
    validate_generated_case,
)


def _foam_header(class_name: str, object_name: str) -> str:
    return (
        "FoamFile\n{\n"
        "    version 2.0;\n    format ascii;\n"
        f"    class {class_name};\n    object {object_name};\n"
        "}\n"
    )


def valid_payload() -> dict[str, object]:
    return {
        "experiment_name": "Backward-facing step study",
        "objective": "Resolve reattachment length for a bounded laminar step flow.",
        "solver": "incompressibleFluid",
        "preprocessing": ["blockMesh", "checkMesh"],
        "parameters": [
            {
                "name": "inlet_velocity",
                "kind": "float",
                "unit": "m/s",
                "minimum": 0.1,
                "maximum": 2.0,
                "default": 0.5,
                "regression_values": [0.25, 1.0],
            },
            {
                "name": "cell_count",
                "kind": "integer",
                "minimum": 8,
                "maximum": 128,
                "default": 32,
                "regression_values": [16, 64],
            },
            {
                "name": "wall_mode",
                "kind": "enum",
                "default": "noSlip",
                "regression_values": ["noSlip", "slip"],
                "allowed_values": ["noSlip", "slip"],
            },
        ],
        "files": [
            {
                "path": "0/U",
                "content": _foam_header("volVectorField", "U")
                + "internalField uniform ({{ inlet_velocity }} 0 0);\n",
            },
            {
                "path": "0/p",
                "content": _foam_header("volScalarField", "p")
                + "internalField uniform 0;\n",
            },
            {
                "path": "constant/physicalProperties",
                "content": _foam_header("dictionary", "physicalProperties")
                + "nu [0 2 -1 0 0 0 0] 1e-5;\n",
            },
            {
                "path": "system/controlDict",
                "content": _foam_header("dictionary", "controlDict")
                + "application incompressibleFluid;\nendTime 1;\n",
            },
            {
                "path": "system/fvSchemes",
                "content": _foam_header("dictionary", "fvSchemes")
                + "ddtSchemes { default steadyState; }\n",
            },
            {
                "path": "system/fvSolution",
                "content": _foam_header("dictionary", "fvSolution") + "solvers {}\n",
            },
            {
                "path": "system/blockMeshDict",
                "content": _foam_header("dictionary", "blockMeshDict")
                + "convertToMeters 1;\nvertices ();\n"
                + "cells {{ cell_count }};\nwallMode {{ wall_mode }};\n",
            },
        ],
        "requested_outputs": ["reattachment_length", "residuals"],
        "assumptions": ["Two-dimensional incompressible flow"],
        "limitations": ["Pilot resolution is not grid independent"],
    }


def draft() -> GeneratedCaseDraft:
    return GeneratedCaseDraft.model_validate(valid_payload())


def replace_file(
    *, path: str | None = None, content: str | None = None, original: str = "system/controlDict"
) -> GeneratedCaseDraft:
    payload = valid_payload()
    for item in payload["files"]:
        if item["path"] == original:
            if path is not None:
                item["path"] = path
            if content is not None:
                item["content"] = content
            break
    return GeneratedCaseDraft.model_validate(payload)


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "../system/controlDict",
        "system/../controlDict",
        "system\\controlDict",
        "C:/system/controlDict",
        "//server/share/controlDict",
        "run.sh",
        "system/.run.sh",
        "system/run.PY",
        "system/control\x00Dict",
        "system/control\u202eDict",
    ],
)
def test_rejects_unsafe_paths_without_leaking_content(unsafe_path: str) -> None:
    secret = "TOP-SECRET-CONTENT"
    with pytest.raises((GeneratedCaseRejected, ValueError)) as raised:
        validate_generated_case(replace_file(path=unsafe_path, content=secret))
    assert secret not in str(raised.value)


def test_rejects_case_insensitive_duplicate_paths() -> None:
    payload = valid_payload()
    payload["files"].append(
        {"path": "system/CONTROLDICT", "content": _foam_header("dictionary", "x")}
    )
    with pytest.raises(GeneratedCaseRejected, match="duplicate"):
        validate_generated_case(GeneratedCaseDraft.model_validate(payload))


def test_manifest_cannot_express_archive_links() -> None:
    payload = valid_payload()
    payload["files"][0]["link"] = "/etc/passwd"
    with pytest.raises(ValidationError, match="Extra inputs"):
        GeneratedCaseDraft.model_validate(payload)


@pytest.mark.parametrize(
    "unsafe",
    [
        "#!/bin/sh\necho owned",
        "#codeStream { code #{ system(\"id\"); #}; }",
        "#calc \"1 + 1\"",
        "dynamicCode {}",
        "codedFixedValue {}",
        "code #{ int x; #};",
        "systemCall touch_owned;",
        "execute /bin/sh;",
        'libs ("libCustom.so");',
        'dlopen("libCustom.so");',
        '#include "/etc/passwd"',
        '#includeEtc "../../etc/passwd"',
        '#include "$HOME/secret"',
        "value $(touch owned);",
        "value `id`;",
        "application simpleFoam;",
        "bad\x00secret",
        "bad\x01secret",
    ],
)
def test_rejects_unsafe_dictionary_content(unsafe: str) -> None:
    content = _foam_header("dictionary", "controlDict") + unsafe
    with pytest.raises(GeneratedCaseRejected):
        validate_generated_case(replace_file(content=content))


def test_scanner_ignores_forbidden_words_in_ordinary_comments() -> None:
    content = (
        _foam_header("dictionary", "controlDict")
        + "application incompressibleFluid;\n"
        + "// systemCall and libCustom.so are prohibited examples\n"
        + "/* #codeStream is forbidden in generated cases */\n"
    )
    assert validate_generated_case(replace_file(content=content)).manifest.solver == (
        "incompressibleFluid"
    )


@pytest.mark.parametrize(
    "include_path",
    ["/etc/passwd", "../../etc/passwd", "relative/$HOME/secret", "relative/*.dict"],
)
def test_rejects_unsafe_inline_include(include_path: str) -> None:
    content = (
        _foam_header("dictionary", "controlDict")
        + "application incompressibleFluid;\n"
        + f'functions {{ #include "{include_path}" }}\n'
    )

    with pytest.raises(GeneratedCaseRejected, match="include"):
        validate_generated_case(replace_file(content=content))


@pytest.mark.parametrize(
    "directive",
    ['#include "fluidScientist/functions"', "#includeEtc <caseDicts/functions>"],
)
def test_allows_exact_safe_inline_relative_include(directive: str) -> None:
    content = (
        _foam_header("dictionary", "controlDict")
        + "application incompressibleFluid;\n"
        + f"functions {{ {directive} }}\n"
    )

    assert validate_generated_case(replace_file(content=content)).manifest.solver == (
        "incompressibleFluid"
    )


@pytest.mark.parametrize(
    "directive",
    [
        "#include relative/path",
        '#include "relative/path" trailingInjection',
        '#include "relative/path" "second/path"',
        '#include "relative/path" #include "second/path"',
        '#include "relative/path"junk',
        '#include <relative/path"',
    ],
)
def test_rejects_malformed_or_ambiguous_inline_include(directive: str) -> None:
    content = (
        _foam_header("dictionary", "controlDict")
        + "application incompressibleFluid;\n"
        + f"functions {{ {directive} }}\n"
    )

    with pytest.raises(GeneratedCaseRejected, match="include"):
        validate_generated_case(replace_file(content=content))


def test_rejects_unterminated_block_comment_in_generated_file() -> None:
    content = (
        _foam_header("dictionary", "controlDict")
        + "application incompressibleFluid;\n"
        + "/* comment never closes"
    )

    with pytest.raises(GeneratedCaseRejected, match="unterminated comment"):
        validate_generated_case(replace_file(content=content))


@pytest.mark.parametrize(
    ("template", "values"),
    [
        ("{{ unknown_name }}", None),
        ("{{ inlet_velocity | shell }}", None),
        ("{{ inlet_velocity + 1 }}", None),
        ("{% for x in y %}", None),
        ("{{{ inlet_velocity }}}", None),
        ("{{ inlet_velocity }}", {"inlet_velocity": 0.5}),
        (
            "{{ inlet_velocity }}",
            {"inlet_velocity": 3.0, "cell_count": 32, "wall_mode": "noSlip"},
        ),
        (
            "{{ inlet_velocity }}",
            {"inlet_velocity": "0.5", "cell_count": 32, "wall_mode": "noSlip"},
        ),
        (
            "{{ inlet_velocity }}",
            {
                "inlet_velocity": 0.5,
                "cell_count": 32,
                "wall_mode": "noSlip",
                "extra": 1,
            },
        ),
    ],
)
def test_rejects_unsafe_placeholders_and_values(
    template: str, values: dict[str, object] | None
) -> None:
    candidate = replace_file(original="0/U", content=_foam_header("volVectorField", "U") + template)
    with pytest.raises(GeneratedCaseRejected):
        render_generated_case(candidate, values)


def test_renders_only_declared_bounded_scalars_and_defaults() -> None:
    rendered = render_generated_case(
        draft(), {"inlet_velocity": 0.25, "cell_count": 64, "wall_mode": "slip"}
    )
    assert "uniform (0.25 0 0)" in rendered.files_by_path["0/U"]
    assert "cells 64;" in rendered.files_by_path["system/blockMeshDict"]
    assert "wallMode slip;" in rendered.files_by_path["system/blockMeshDict"]
    assert "0.5" in render_defaults(draft()).files_by_path["0/U"]
    assert "{{" in draft().files[0].content  # immutable source template remains intact


@pytest.mark.parametrize(
    ("path", "class_name"),
    [
        ("0/U", "volScalarField"),
        ("0/p", "volVectorField"),
        ("system/controlDict", "volScalarField"),
    ],
)
def test_requires_mandatory_files_and_correct_foam_classes(path: str, class_name: str) -> None:
    object_name = path.rsplit("/", 1)[-1]
    with pytest.raises(GeneratedCaseRejected):
        validate_generated_case(
            replace_file(original=path, content=_foam_header(class_name, object_name))
        )


def test_requires_all_mandatory_files() -> None:
    payload = valid_payload()
    payload["files"] = [item for item in payload["files"] if item["path"] != "0/p"]
    with pytest.raises(GeneratedCaseRejected, match="mandatory"):
        validate_generated_case(GeneratedCaseDraft.model_validate(payload))


def test_requires_matching_foam_object_name() -> None:
    with pytest.raises(GeneratedCaseRejected, match="object"):
        validate_generated_case(
            replace_file(
                original="0/U", content=_foam_header("volVectorField", "notTheField")
            )
        )


def test_packaging_is_deterministic_and_downstream_validated() -> None:
    first = validate_generated_case(draft())
    second = validate_generated_case(draft())
    assert first.archive == second.archive
    assert first.archive_sha256 == second.archive_sha256
    assert first.manifest.archive_sha256 == first.archive_sha256
    assert first.preprocessing == ("blockMesh", "checkMesh")
    assert first.archive[3] & 0x08 == 0  # no environment-dependent gzip filename
    assert first.archive[4:8] == b"\x00\x00\x00\x00"

    with gzip.GzipFile(fileobj=io.BytesIO(first.archive), mode="rb") as compressed:
        tar_bytes = compressed.read()
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:") as bundle:
        members = bundle.getmembers()
        assert [member.name for member in members] == sorted(member.name for member in members)
        assert all(member.isfile() for member in members)
        assert all(member.uid == member.gid == member.mtime == 0 for member in members)
        assert all(member.uname == member.gname == "" for member in members)
        assert all(member.mode == 0o644 for member in members)


def test_archive_size_limit_is_enforced_without_leaking_content() -> None:
    with pytest.raises(GeneratedCaseRejected, match="size") as raised:
        validate_generated_case(draft(), max_archive_bytes=16)
    assert "TOP-SECRET" not in str(raised.value)
