import io
import tarfile

import pytest

from fluid_scientist.adapters.custom_openfoam import (
    CustomCaseRejected,
    validate_custom_case_archive,
)


def archive(files: dict[str, str], *, symlink: tuple[str, str] | None = None) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as bundle:
        for name, text in files.items():
            payload = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            bundle.addfile(info, io.BytesIO(payload))
        if symlink:
            info = tarfile.TarInfo(symlink[0])
            info.type = tarfile.SYMTYPE
            info.linkname = symlink[1]
            bundle.addfile(info)
    return output.getvalue()


def valid_files() -> dict[str, str]:
    return {
        "0/U": "FoamFile {}\ninternalField uniform (0 0 0);",
        "0/p": "FoamFile {}\ninternalField uniform 0;",
        "constant/physicalProperties": "FoamFile {}\nnu 1e-6;",
        "system/controlDict": "FoamFile {}\nsolver incompressibleFluid;\nendTime 500;",
        "system/fvSchemes": "FoamFile {}\nddtSchemes {}",
        "system/fvSolution": "FoamFile {}\nsolvers {}",
        "system/blockMeshDict": "FoamFile {}\nvertices ();",
    }


def test_valid_custom_case_returns_immutable_manifest() -> None:
    result = validate_custom_case_archive(archive(valid_files()))

    assert result.solver == "incompressibleFluid"
    assert result.needs_block_mesh is True
    assert result.has_mesh is False
    assert result.archive_sha256.startswith("sha256:")
    assert "system/controlDict" in result.members


def test_custom_case_detects_fixed_mirror_mesh_preprocessing() -> None:
    files = {**valid_files(), "system/mirrorMeshDict": "planeType pointAndNormal;"}

    result = validate_custom_case_archive(archive(files))

    assert result.needs_mirror_mesh is True


@pytest.mark.parametrize(
    "files, message",
    [
        ({"../system/controlDict": "solver incompressibleFluid;"}, "path"),
        ({**valid_files(), "system/controlDict": "solver pisoFoam;"}, "solver"),
        (
            {**valid_files(), "0/U": "type codedFixedValue; code #{ system(\"id\"); #};"},
            "dynamic code",
        ),
    ],
)
def test_custom_case_rejects_unsafe_content(files, message) -> None:
    with pytest.raises(CustomCaseRejected, match=message):
        validate_custom_case_archive(archive(files))


def test_custom_case_rejects_links() -> None:
    with pytest.raises(CustomCaseRejected, match="link"):
        validate_custom_case_archive(
            archive(valid_files(), symlink=("constant/polyMesh", "/etc"))
        )


def test_custom_case_ignores_forbidden_construct_names_in_comments() -> None:
    files = valid_files()
    files["system/controlDict"] += (
        "\n// #codeStream, #include, and systemCall are forbidden"
        "\n/* codedFixedValue and #includeEtc are inert in a closed block comment */"
    )

    assert validate_custom_case_archive(archive(files)).solver == "incompressibleFluid"


@pytest.mark.parametrize("dangerous", ["systemCall touch_owned;", "type codedFixedValue;"])
def test_unterminated_comment_cannot_hide_later_archive_member(dangerous: str) -> None:
    files = valid_files()
    files["system/controlDict"] += "\n/* comment never closes"
    files["system/fvSchemes"] += f"\n{dangerous}"

    with pytest.raises(CustomCaseRejected, match="unterminated comment"):
        validate_custom_case_archive(archive(files))


@pytest.mark.parametrize(
    "dangerous",
    [
        '#include "relative/path"',
        "#includeEtc <caseDicts/functions>",
        '#includeIfPresent "optional"',
        '#includeFunc "functionObject"',
        '# include "relative/path"',
        '# InClUdE "relative/path"',
        '#calc "1 + 1"',
        "#eval{ 1 + 1 }",
        'libs ("libCustom.so");',
        "dlopen libCustom.so;",
        "command harmless-looking;",
        'program "/bin/sh";',
        'value "$(id)";',
    ],
)
def test_custom_case_rejects_shared_forbidden_dictionary_constructs(
    dangerous: str,
) -> None:
    files = valid_files()
    files["system/fvSchemes"] += "\n" + dangerous

    with pytest.raises(CustomCaseRejected):
        validate_custom_case_archive(archive(files))


@pytest.mark.parametrize(
    "control_body",
    [
        'note "solver incompressibleFluid;";',
        "solver $selected;",
        "application ${selected};",
        "$solver incompressibleFluid;",
        "solver incompressibleFluid; application incompressibleFluid;",
        "solver simpleFoam; solver incompressibleFluid;",
        "endTime 1;",
    ],
)
def test_custom_case_requires_one_operative_literal_solver(control_body: str) -> None:
    files = valid_files()
    files["system/controlDict"] = "FoamFile {}\n" + control_body

    with pytest.raises(CustomCaseRejected, match="solver"):
        validate_custom_case_archive(archive(files))


@pytest.mark.parametrize(
    "suffix", [".tar.gz", ".tar", ".tgz", ".zip", ".7z", ".bz2", ".xz", ".gz"]
)
def test_custom_case_rejects_archive_and_compression_members(suffix: str) -> None:
    files = {**valid_files(), f"fluidScientist/input{suffix}": "data"}

    with pytest.raises(CustomCaseRejected, match="archive"):
        validate_custom_case_archive(archive(files))


def test_custom_case_rejects_oversized_utf8_component_and_total_path() -> None:
    oversized_component = "界" * 240
    long_path = "/".join(["segment"] * 600)
    for unsafe_path in (
        f"fluidScientist/{oversized_component}",
        f"fluidScientist/{long_path}",
    ):
        files = {**valid_files(), unsafe_path: "data"}
        with pytest.raises(CustomCaseRejected, match="path"):
            validate_custom_case_archive(archive(files))


def test_custom_case_errors_do_not_leak_authored_content() -> None:
    secret = "TOP-SECRET-AUTHORED-VALUE"
    files = valid_files()
    files["system/fvSchemes"] += f"\n# include \"{secret}\""

    with pytest.raises(CustomCaseRejected) as raised:
        validate_custom_case_archive(archive(files))

    assert secret not in str(raised.value)
