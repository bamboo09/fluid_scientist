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
    files["system/controlDict"] += "\n// #codeStream and systemCall are forbidden"

    assert validate_custom_case_archive(archive(files)).solver == "incompressibleFluid"
